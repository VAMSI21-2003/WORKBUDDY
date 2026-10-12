"""Follow-up turns through the API (``docs/design/follow-up-turns.md``).

A request builds on the latest file Buddy delivered from the same upload: "remove the duplicates",
then "now remove the empty rows too", gives one file with both done. Brain's reading ("start
over", "use my original file") sends a turn back to the upload; a new upload, or a file the person
picks, is used as itself; a copy that already satisfies the request is said so and nothing new is
handed back. Every turn runs the real pipeline (real skills, verifiers and speakers; only the LLM
is faked), and every number in every reply is checked against an independent read, with openpyxl,
of the file the turn actually used and of the file it delivered, both downloaded through the API.
"""

from __future__ import annotations

import hashlib
import io
import json
import re
import stat
from pathlib import Path
from typing import Any

import openpyxl
import pytest
from fastapi.testclient import TestClient

from backend.main import create_app
from backend.sessions import session_key
from backend.settings import Settings
from backend.store import SessionStore
from tests.backend.factories import SPEAKERS, PipelineFactory, QueuedProvider, raise_in, single
from tests.backend.helpers import new_session, start_turn, upload, wait_finished, xlsx_bytes
from tests.backend.signin_helpers import ALICE, BOB, Rig
from tests.file_sentences import worked_on_original, worked_on_original_skipping_copy

SIX_ROWS = xlsx_bytes()  # 6 rows: 2 duplicates (Alice, Bob), 1 fully-empty row


def facts(data: bytes) -> tuple[int, int, int]:
    """``(rows, rows that repeat an earlier row, fully-empty rows)``, read independently."""
    sheet = openpyxl.load_workbook(io.BytesIO(data), read_only=True).worksheets[0]
    body = [tuple(row) for row in sheet.iter_rows(min_row=2, values_only=True)]
    seen: set[tuple[object, ...]] = set()
    repeats = 0
    for row in body:
        repeats += row in seen
        seen.add(row)
    return len(body), repeats, sum(all(cell is None for cell in row) for row in body)


def numbers_in(reply: str) -> set[int]:
    """The numbers in a reply, not counting a file name or a percentage."""
    text = re.sub(r"[\w\-. ]*[\w\-]\.xlsx", "", reply)
    return {int(n) for n in re.findall(r"\d+", re.sub(r"\d+(?:\.\d+)?%", "", text))}


def allowed(used: tuple[int, int, int], result: tuple[int, int, int] | None) -> set[int]:
    numbers = set(used)
    if result is not None:
        numbers |= set(result)
        numbers |= {a - b for a, b in zip(used, result, strict=True)}
    return numbers


def mode_for(speaker: str) -> str:
    return "focus" if speaker == "focus" else "friend"


class Chat:
    """One person's session on one app: uploads, turns, and independent reads of the files."""

    def __init__(self, client: TestClient, factory: PipelineFactory, speaker: str = "friend"):
        self.client = client
        self.factory = factory
        self.session_id = new_session(client, mode_for(speaker))
        self.uploads: list[dict[str, Any]] = []

    def upload(self, data: bytes = SIX_ROWS, name: str = "report.xlsx") -> dict[str, Any]:
        response = upload(self.client, self.session_id, data, name)
        assert response.status_code == 201, response.text
        self.uploads.append(response.json())
        return dict(response.json())

    def turn(self, message: str, decision: str | None = "approve", **extra: Any) -> dict[str, Any]:
        started = start_turn(self.client, self.session_id, message, **extra)
        assert started.status_code == 202, started.text
        return wait_finished(
            self.client, self.session_id, started.json()["turn_id"], decision=decision
        )

    def download(self, delivered: dict[str, Any]) -> bytes:
        response = self.client.get(delivered["download_url"])
        assert response.status_code == 200, response.text
        return response.content

    def state(self) -> dict[str, Any]:
        return dict(self.client.get(f"/api/v1/sessions/{self.session_id}").json())

    def delivered_names(self) -> list[str]:
        files = self.client.get(f"/api/v1/sessions/{self.session_id}/files").json()["files"]
        return [f["name"] for f in files if f["kind"] == "delivered"]


@pytest.fixture
def make_chat(settings: Settings):  # type: ignore[no-untyped-def]
    def make(replies: list[str], speaker: str = "friend", **factory_args: Any) -> Chat:
        factory = PipelineFactory(QueuedProvider(replies), speaker=speaker, **factory_args)
        client = TestClient(create_app(settings, factory))
        chat = Chat(client, factory, speaker)
        chat.settings = settings  # type: ignore[attr-defined]
        return chat

    return make


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


# -- Two and three requests adding up ------------------------------------------------------------


@pytest.mark.parametrize("speaker", SPEAKERS)
def test_two_requests_add_up_in_one_file(make_chat, speaker: str) -> None:  # type: ignore[no-untyped-def]
    chat = make_chat(single("remove_duplicate_rows") + single("remove_empty_rows"), speaker)
    chat.upload()
    upload_path = chat.settings.uploads_dir.rglob("report.xlsx")
    upload_hash = sha(next(upload_path))

    first = chat.turn("remove the duplicate rows")
    (copy_1,) = first["delivered_files"]
    assert facts(chat.download(copy_1)) == (4, 0, 1)
    assert worked_on_original() in first["reply"]
    assert chat.state()["next_request_file"] == {
        "kind": "delivered",
        "file_id": copy_1["file_id"],
        "name": copy_1["name"],
    }

    second = chat.turn("now remove the empty rows too")
    (copy_2,) = second["delivered_files"]

    assert second["outcome"] == "delivered"
    assert facts(chat.download(copy_2)) == (3, 0, 0)  # both done, in the one file
    assert f"`{copy_1['name']}`, the latest copy I handed back" in second["reply"]
    assert numbers_in(second["reply"]) <= allowed(
        facts(chat.download(copy_1)), facts(chat.download(copy_2))
    )
    # What each turn recorded it worked on.
    state = chat.state()
    assert state["next_request_file"]["file_id"] == copy_2["file_id"]
    assert copy_2["name"] == "report_deduplicated_cleaned.xlsx"
    assert sha(next(chat.settings.uploads_dir.rglob("report.xlsx"))) == upload_hash


@pytest.mark.parametrize("speaker", SPEAKERS)
def test_three_requests_in_a_row_the_last_has_nothing_left(make_chat, speaker: str) -> None:  # type: ignore[no-untyped-def]
    chat = make_chat(
        single("remove_empty_rows") + single("remove_duplicate_rows") * 2, speaker
    )
    chat.upload()
    first = chat.turn("remove the empty rows")
    (copy_1,) = first["delivered_files"]
    assert facts(chat.download(copy_1)) == (5, 2, 0)

    second = chat.turn("and the duplicates")
    (copy_2,) = second["delivered_files"]
    assert facts(chat.download(copy_2)) == (3, 0, 0)
    assert numbers_in(second["reply"]) <= allowed(facts(chat.download(copy_1)), (3, 0, 0))

    before = chat.delivered_names()
    third = chat.turn("remove the duplicates again")

    assert third["outcome"] == "not_delivered" and third["delivered_files"] == []
    assert (
        f"The latest copy, `{copy_2['name']}`, already has no exact duplicate rows, so there was "
        "nothing to remove and I handed back no new file."
    ) in third["reply"]
    assert not numbers_in(third["reply"])
    assert chat.delivered_names() == before  # no new file
    assert chat.state()["next_request_file"]["file_id"] == copy_2["file_id"]  # still the latest


# -- Start over, a new upload, a picked file -----------------------------------------------------


def test_start_over_works_on_the_upload_and_later_requests_build_on_that(make_chat) -> None:  # type: ignore[no-untyped-def]
    chat = make_chat(
        single("remove_duplicate_rows")
        + single("remove_empty_rows", file_to_use="original")
        + single("remove_duplicate_rows")
    )
    chat.upload()
    first = chat.turn("remove the duplicate rows")

    again = chat.turn("start over and remove the empty rows")
    (copy_b,) = again["delivered_files"]

    assert facts(chat.download(copy_b)) == (5, 2, 0)  # the upload's duplicates are all still there
    assert worked_on_original_skipping_copy() in again["reply"]
    assert numbers_in(again["reply"]) <= allowed((6, 2, 1), (5, 2, 0))
    assert chat.state()["next_request_file"]["file_id"] == copy_b["file_id"]
    assert first["delivered_files"][0]["file_id"] != copy_b["file_id"]

    third = chat.turn("now the duplicates")
    (copy_c,) = third["delivered_files"]

    assert facts(chat.download(copy_c)) == (3, 0, 0)
    assert f"`{copy_b['name']}`, the latest copy I handed back" in third["reply"]


@pytest.mark.parametrize("speaker", SPEAKERS)
@pytest.mark.parametrize("intent", ["unclear", "conversation", "report_on_file"])
def test_a_turn_that_hands_nothing_back_never_changes_what_comes_next(
    make_chat, speaker: str, intent: str  # type: ignore[no-untyped-def]
) -> None:
    """Review round 1, F1: "use my original file" alone is answered as a turn that did nothing,
    and it is one. The next request still builds on the latest copy; the page's line agrees."""
    chat = make_chat(
        single("remove_duplicate_rows")
        + single(intent, file_to_use="original")
        + single("remove_empty_rows"),
        speaker,
    )
    chat.upload()
    (copy_1,) = chat.turn("remove the duplicate rows")["delivered_files"]

    nothing = chat.turn("use my original file")
    assert nothing["outcome"] == "not_delivered" and nothing["delivered_files"] == []
    assert chat.state()["next_request_file"]["file_id"] == copy_1["file_id"]

    after = chat.turn("remove the empty rows")
    (copy,) = after["delivered_files"]

    assert facts(chat.download(copy)) == (3, 0, 0)  # built on the copy
    assert f"`{copy_1['name']}`, the latest copy I handed back" in after["reply"]


@pytest.mark.parametrize("speaker", SPEAKERS)
def test_a_declined_start_over_changes_nothing_either(make_chat, speaker: str) -> None:  # type: ignore[no-untyped-def]
    chat = make_chat(
        single("remove_duplicate_rows")
        + single("remove_empty_rows", file_to_use="original")
        + single("remove_empty_rows"),
        speaker,
    )
    chat.upload()
    (copy_1,) = chat.turn("remove the duplicate rows")["delivered_files"]

    declined = chat.turn("start over and remove the empty rows", decision="reject")
    assert declined["outcome"] == "not_delivered"
    after = chat.turn("remove the empty rows")

    assert f"`{copy_1['name']}`, the latest copy I handed back" in after["reply"]


@pytest.mark.parametrize("speaker", SPEAKERS)
def test_a_failed_turn_that_read_start_over_changes_nothing(make_chat, speaker: str) -> None:  # type: ignore[no-untyped-def]
    calls = {"n": 0}

    def wrap(cycle: Any) -> Any:
        calls["n"] += 1
        return raise_in("inspector", after=True)(cycle) if calls["n"] == 2 else cycle

    chat = make_chat(
        single("remove_duplicate_rows")
        + single("remove_empty_rows", file_to_use="original")
        + single("remove_empty_rows"),
        speaker,
        wrap=wrap,
    )
    chat.upload()
    (copy_1,) = chat.turn("remove the duplicate rows")["delivered_files"]

    failed = chat.turn("start over and remove the empty rows")
    assert failed["outcome"] == "failed"
    after = chat.turn("remove the empty rows")

    assert f"`{copy_1['name']}`, the latest copy I handed back" in after["reply"]
    assert facts(chat.download(after["delivered_files"][0])) == (3, 0, 0)


@pytest.mark.parametrize("speaker", ["friend", "focus"])
def test_the_reply_names_the_file_the_turn_worked_on_when_two_deliveries_share_a_name(
    make_chat, speaker: str  # type: ignore[no-untyped-def]
) -> None:
    """Review round 1, F2: both sentences of the reply name the same file."""
    chat = make_chat(
        single("remove_duplicate_rows")
        + single("remove_duplicate_rows", file_to_use="original")
        + single("remove_empty_rows"),
        speaker,
    )
    chat.upload()
    chat.turn("remove the duplicate rows")
    (second,) = chat.turn("start over, remove the duplicate rows")["delivered_files"]
    assert second["name"] == "report_deduplicated (2).xlsx"

    third = chat.turn("now remove the empty rows")

    assert "Your cleaned copy of report_deduplicated (2).xlsx is ready." in third["reply"]
    assert "`report_deduplicated (2).xlsx`, the latest copy I handed back" in third["reply"]
    assert "report_deduplicated.xlsx" not in third["reply"]


def test_a_new_upload_in_the_middle_is_used_as_itself(make_chat) -> None:  # type: ignore[no-untyped-def]
    chat = make_chat(
        single("remove_duplicate_rows") * 2
        + single("remove_empty_rows")
        + single("remove_duplicate_rows")
    )
    first_upload = chat.upload()
    first = chat.turn("remove the duplicate rows")
    (copy_1,) = first["delivered_files"]
    rows = {"x": [1, 1, None, 2], "y": ["a", "a", None, "b"]}
    other = chat.upload(xlsx_bytes(rows), "other.xlsx")
    assert chat.state()["next_request_file"] == {
        "kind": "upload",
        "file_id": other["file_id"],
        "name": "other.xlsx",
    }

    second = chat.turn("remove the duplicate rows")
    (copy_o,) = second["delivered_files"]

    assert facts(chat.download(copy_o)) == (3, 0, 1)
    assert worked_on_original("other.xlsx") in second["reply"]
    assert numbers_in(second["reply"]) <= allowed((4, 1, 1), (3, 0, 1))

    third = chat.turn("remove the empty rows")  # the new upload's own follow-up
    assert f"`{copy_o['name']}`, the latest copy I handed back" in third["reply"]
    assert copy_1["name"] not in third["reply"]

    # The person picks the first upload again: it is used as itself, not its copy.
    picked = chat.turn("remove the duplicate rows", file_id=first_upload["file_id"])
    assert worked_on_original_skipping_copy() in picked["reply"]
    (copy_p,) = picked["delivered_files"]
    assert facts(chat.download(copy_p)) == (4, 0, 1)
    assert chat.state()["active_file_id"] == first_upload["file_id"]
    assert chat.state()["next_request_file"]["file_id"] == copy_p["file_id"]


# -- After a turn that failed or was rejected -----------------------------------------------------


def test_a_follow_up_after_a_failed_turn_uses_the_last_delivered_file(make_chat) -> None:  # type: ignore[no-untyped-def]
    calls = {"n": 0}

    def wrap(cycle: Any) -> Any:
        calls["n"] += 1
        return raise_in("inspector", after=True)(cycle) if calls["n"] == 2 else cycle

    chat = make_chat(single("remove_duplicate_rows") * 2 + single("remove_empty_rows"), wrap=wrap)
    chat.upload()
    first = chat.turn("remove the duplicate rows")
    (copy_1,) = first["delivered_files"]

    failed = chat.turn("remove the duplicate rows once more")
    assert failed["outcome"] == "failed" and failed["delivered_files"] == []
    assert chat.state()["next_request_file"]["file_id"] == copy_1["file_id"]

    third = chat.turn("now the empty rows")
    (copy_3,) = third["delivered_files"]

    assert facts(chat.download(copy_3)) == (3, 0, 0)
    assert f"`{copy_1['name']}`, the latest copy I handed back" in third["reply"]
    assert numbers_in(third["reply"]) <= allowed(facts(chat.download(copy_1)), (3, 0, 0))


def test_a_follow_up_after_a_rejected_plan_uses_the_last_delivered_file(make_chat) -> None:  # type: ignore[no-untyped-def]
    chat = make_chat(single("remove_duplicate_rows") + single("remove_empty_rows") * 2)
    chat.upload()
    (copy_1,) = chat.turn("remove the duplicate rows")["delivered_files"]

    rejected = chat.turn("now the empty rows", decision="reject")
    assert rejected["outcome"] == "not_delivered" and rejected["delivered_files"] == []
    assert chat.state()["next_request_file"]["file_id"] == copy_1["file_id"]

    third = chat.turn("now the empty rows")
    (copy_3,) = third["delivered_files"]

    assert facts(chat.download(copy_3)) == (3, 0, 0)
    assert f"`{copy_1['name']}`, the latest copy I handed back" in third["reply"]


def test_a_greeting_between_two_requests_changes_nothing(make_chat) -> None:  # type: ignore[no-untyped-def]
    chat = make_chat(
        single("remove_duplicate_rows") + single("conversation") + single("remove_empty_rows")
    )
    chat.upload()
    (copy_1,) = chat.turn("remove the duplicate rows")["delivered_files"]

    chat.turn("thanks!")
    third = chat.turn("now the empty rows")

    assert f"`{copy_1['name']}`, the latest copy I handed back" in third["reply"]


# -- Another person's session ---------------------------------------------------------------------


def test_another_persons_session_neither_offers_nor_receives_a_copy(tmp_path: Path) -> None:
    factory = PipelineFactory(
        QueuedProvider(
            single("remove_duplicate_rows") * 2 + single("remove_empty_rows")
        ),
        speaker="friend",
    )
    rig = Rig(tmp_path, factory)
    alice, bob = Chat(rig.person(ALICE), factory), Chat(rig.person(BOB), factory)
    alice.upload()
    (alices_copy,) = alice.turn("remove the duplicate rows")["delivered_files"]
    bob.upload()

    # Bob can't start a turn in Alice's session, and it changes nothing for her.
    refused = start_turn(bob.client, alice.session_id, "remove the empty rows")
    assert refused.status_code == 404
    assert bob.client.get(alices_copy["download_url"]).status_code == 404
    assert alice.state()["turns"][-1]["turn_id"] == "t0001"

    # Bob's first request works on his own upload, however Alice's session stands.
    bobs = bob.turn("remove the duplicate rows")
    (bobs_copy,) = bobs["delivered_files"]
    assert worked_on_original() in bobs["reply"]
    assert alice.session_id not in json.dumps(bob.state())
    assert [t["turn_id"] for t in bob.state()["turns"]] == ["t0001"]
    assert bob.state()["next_request_file"]["name"] == bobs_copy["name"]
    assert facts(bob.download(bobs_copy)) == (4, 0, 1)

    # Alice's follow-up still builds on her own copy.
    hers = alice.turn("now the empty rows")
    assert f"`{alices_copy['name']}`, the latest copy I handed back" in hers["reply"]
    assert facts(alice.download(hers["delivered_files"][0])) == (3, 0, 0)


# -- What is recorded, and the files kept as they were ---------------------------------------------


def test_each_turn_records_the_file_it_used_and_a_restart_keeps_it(
    settings: Settings,
) -> None:
    provider = QueuedProvider(
        single("remove_duplicate_rows")
        + single("remove_empty_rows", file_to_use="original")
        + single("remove_duplicate_rows")
    )
    factory = PipelineFactory(provider, speaker="friend")
    client = TestClient(create_app(settings, factory))
    chat = Chat(client, factory)
    upload_record = chat.upload()
    (copy_1,) = chat.turn("remove the duplicate rows")["delivered_files"]
    second = chat.turn("start over and remove the empty rows")
    key = session_key(chat.session_id)

    def stored(turn_id: str) -> dict[str, Any]:
        path = settings.sessions_dir / key / "turns" / f"{turn_id}.json"
        return dict(json.loads(path.read_text(encoding="utf-8")))

    assert stored("t0001")["file_used"] == {
        "kind": "upload",
        "file_id": upload_record["file_id"],
        "start_over": False,
    }
    assert stored("t0002")["file_used"] == {
        "kind": "upload",
        "file_id": upload_record["file_id"],
        "start_over": True,
    }

    # A new server on the same data: the next request builds on what start-over delivered.
    restarted = TestClient(create_app(settings, PipelineFactory(provider, speaker="friend")))
    chat.client = restarted
    third = chat.turn("now the duplicates")
    (copy_3,) = third["delivered_files"]
    assert f"`{second['delivered_files'][0]['name']}`, the latest copy I handed back" in (
        third["reply"]
    )
    assert stored("t0003")["file_used"] == {
        "kind": "delivered",
        "file_id": second["delivered_files"][0]["file_id"],
        "start_over": False,
    }
    assert copy_1["file_id"] != copy_3["file_id"]


def test_the_delivered_file_and_the_upload_are_never_changed_by_a_follow_up(
    make_chat,  # type: ignore[no-untyped-def]
) -> None:
    chat = make_chat(single("remove_duplicate_rows") + single("remove_empty_rows"))
    chat.upload()
    (copy_1,) = chat.turn("remove the duplicate rows")["delivered_files"]
    settings: Settings = chat.settings  # type: ignore[attr-defined]
    (copy_1_path,) = [
        p for p in settings.outputs_dir.rglob("*.xlsx") if p.parent.name == "t0001"
    ]
    (upload_path,) = settings.uploads_dir.rglob("report.xlsx")
    before = (sha(copy_1_path), sha(upload_path))

    second = chat.turn("now the empty rows")

    assert second["outcome"] == "delivered"
    assert (sha(copy_1_path), sha(upload_path)) == before
    assert sha(copy_1_path) == copy_1["sha256"]
    # The turn read its own read-only copy, inside its own folder, byte for byte the delivery.
    (work,) = (settings.outputs_dir / session_key(chat.session_id) / "t0002" / "input").iterdir()
    assert work.read_bytes() == copy_1_path.read_bytes()
    assert not work.stat().st_mode & (stat.S_IWUSR | stat.S_IWGRP | stat.S_IWOTH)
    assert work.name == "report_deduplicated.xlsx"  # a short name, whatever the stored one is


def test_a_delivered_file_that_is_gone_is_not_pretended_to_be_used(make_chat) -> None:  # type: ignore[no-untyped-def]
    chat = make_chat(single("remove_duplicate_rows") + single("remove_empty_rows"))
    chat.upload()
    chat.turn("remove the duplicate rows")
    settings: Settings = chat.settings  # type: ignore[attr-defined]
    (copy_path,) = [p for p in settings.outputs_dir.rglob("*.xlsx") if p.parent.name == "t0001"]
    copy_path.unlink()

    second = chat.turn("now the empty rows")
    (copy_2,) = second["delivered_files"]

    assert worked_on_original_skipping_copy() in second["reply"]
    assert "the latest copy I handed back" not in second["reply"]
    assert facts(chat.download(copy_2)) == (5, 2, 0)  # it worked on the upload, as the reply says


def test_a_delivered_file_that_changed_on_disk_is_not_used(make_chat) -> None:  # type: ignore[no-untyped-def]
    chat = make_chat(single("remove_duplicate_rows") + single("remove_empty_rows"))
    chat.upload()
    chat.turn("remove the duplicate rows")
    settings: Settings = chat.settings  # type: ignore[attr-defined]
    (copy_path,) = [p for p in settings.outputs_dir.rglob("*.xlsx") if p.parent.name == "t0001"]
    copy_path.chmod(0o644)
    copy_path.write_bytes(copy_path.read_bytes() + b"\0")

    second = chat.turn("now the empty rows")

    assert worked_on_original_skipping_copy() in second["reply"]
    assert second["outcome"] == "delivered"


def test_a_stored_turn_naming_a_file_that_doesnt_exist_is_not_restored(settings: Settings) -> None:
    factory = PipelineFactory(QueuedProvider(single("remove_duplicate_rows")), speaker="friend")
    client = TestClient(create_app(settings, factory))
    chat = Chat(client, factory)
    chat.upload()
    chat.turn("remove the duplicate rows")
    key = session_key(chat.session_id)
    path = settings.sessions_dir / key / "turns" / "t0001.json"
    record = json.loads(path.read_text(encoding="utf-8"))

    for bad in (
        {"kind": "delivered", "file_id": "d_t0009_1", "start_over": False},
        {"kind": "upload", "file_id": "f_nothere", "start_over": False},
        {"kind": "upload", "file_id": record["target_file_id"], "start_over": "yes"},
        {"kind": "elsewhere", "file_id": record["target_file_id"], "start_over": False},
        {"kind": "upload", "file_id": record["target_file_id"]},
    ):
        path.write_text(json.dumps({**record, "file_used": bad}), encoding="utf-8")
        assert SessionStore(settings).load_session(key) is None, bad
    path.write_text(json.dumps(record), encoding="utf-8")
    assert SessionStore(settings).load_session(key) is not None
    # A record without the key at all is the older format, and opens.
    path.write_text(
        json.dumps({k: v for k, v in record.items() if k != "file_used"}), encoding="utf-8"
    )
    assert SessionStore(settings).load_session(key) is not None


# -- Guards a mutation must not get past (review round 1, F3) -----------------------------------


class _Touch:
    """A stage that runs the real one and then appends a byte to files it is given (a breach)."""

    def __init__(self, inner: Any, paths: Any) -> None:
        self._inner = inner
        self._paths = paths

    def __call__(self, state: Any) -> Any:
        state = self._inner(state)
        for path in self._paths(state):
            path.chmod(0o644)
            with open(path, "ab") as handle:
                handle.write(b"\0")
        return state


def _touching(paths: Any) -> Any:
    import dataclasses

    def wrap(cycle: Any) -> Any:
        return dataclasses.replace(cycle, hands=_Touch(cycle.hands, paths))

    return wrap


def _breach(make_chat, which: str) -> tuple[Any, dict[str, Any]]:  # type: ignore[no-untyped-def]
    armed = {"on": False}

    def paths(state: Any) -> list[Path]:
        if not armed["on"] or state.file_choice is None or state.file_choice.copy is None:
            return []
        work = Path(state.file_choice.copy)
        if which == "the turn's copy":
            return [work]
        (delivered,) = (p for p in work.parents[2].rglob("*.xlsx") if p.parent.name == "t0001")
        return [delivered]

    chat = make_chat(single("remove_duplicate_rows") + single("remove_empty_rows") * 2,
                     wrap=_touching(paths))
    chat.upload()
    first = chat.turn("remove the duplicate rows")
    armed["on"] = True
    return chat, first


@pytest.mark.parametrize("which", ["the turn's copy", "the earlier delivered file"])
def test_a_skill_that_rewrites_the_copy_or_the_delivered_file_hands_nothing_back(
    make_chat, which: str  # type: ignore[no-untyped-def]
) -> None:
    chat, _ = _breach(make_chat, which)

    breached = chat.turn("now remove the empty rows")

    assert breached["outcome"] == "integrity_failure" and breached["delivered_files"] == []


def test_a_follow_up_on_a_copy_leaves_a_session_a_restart_can_open(settings: Settings) -> None:
    """The saved context names uploads only: a copy's path or its analysis must not reach it."""
    provider = QueuedProvider(single("remove_duplicate_rows") + single("remove_empty_rows"))
    factory = PipelineFactory(provider, speaker="friend")
    chat = Chat(TestClient(create_app(settings, factory)), factory)
    chat.upload()
    chat.turn("remove the duplicate rows")
    (copy_2,) = chat.turn("now the empty rows")["delivered_files"]

    restarted = TestClient(create_app(settings, PipelineFactory(QueuedProvider([]))))
    state = restarted.get(f"/api/v1/sessions/{chat.session_id}")

    assert state.status_code == 200
    assert state.json()["next_request_file"]["file_id"] == copy_2["file_id"]
    saved = json.loads(
        (settings.sessions_dir / session_key(chat.session_id) / "session.json").read_text()
    )
    uploads = {str(p) for p in settings.uploads_dir.rglob("report.xlsx")}
    assert saved["context"]["active_file"] in uploads
    assert set(saved["context"]["analyzed_files"]) <= uploads
