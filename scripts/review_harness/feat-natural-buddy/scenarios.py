"""Ordinary scenarios for feat/natural-buddy probes (fake Brain/Think/planner; real everything else)."""
from nb import AN, DD, ER, brain, plan, think

DUP = ("remove_duplicate_rows", "remove the duplicate rows")
EMP = ("remove_empty_rows", "remove the empty rows")


def t(msg, b, p=None, **kw):
    d = {"msg": msg, "b": b, "p": p}
    d.update(kw)
    return d


S = {
    "s01-dedupe": {"file": "plain", "turns": [t("remove the duplicate rows", brain(*DUP[:1], DUP), plan((DD, DUP[1])))]},
    "s02-empty": {"file": "plain", "turns": [t("remove the empty rows", brain(*EMP[:1], EMP), plan((ER, EMP[1])))]},
    "s03-both": {"file": "plain", "turns": [t(
        "remove the duplicate rows and then remove the empty rows",
        brain("multi_step_request", DUP, EMP), plan((DD, DUP[1]), (ER, EMP[1])))]},
    "s04-dedupe+sort": {"file": "plain", "turns": [t(
        "remove the duplicate rows and sort by amount",
        brain("multi_step_request", DUP, ("unsupported", "sort by amount")), plan((DD, DUP[1])))]},
    "s05-dedupe+report": {"file": "plain", "turns": [t(
        "remove the duplicate rows and then give me a report",
        brain("multi_step_request", DUP, ("report_on_file", "give me a report")), plan((DD, DUP[1])))]},
    "s06-report": {"file": "plain", "turns": [t(
        "give me a report on this file", brain("report_on_file", ("report_on_file", "give me a report on this file")))]},
    "s07-hi-nofile": {"turns": [t("hey buddy how r u", brain("conversation"))]},
    "s08-hi-file": {"file": "plain", "turns": [t("thanks!", brain("conversation"))]},
    "s09-unclear": {"file": "plain", "turns": [t("do the thing", brain("unclear"))]},
    "s10-clean-dedupe": {"file": "clean", "turns": [t("remove the duplicate rows", brain(*DUP[:1], DUP), plan((DD, DUP[1])))]},
    "s11-followup": {"file": "plain", "turns": [
        t("remove the duplicate rows", brain(*DUP[:1], DUP), plan((DD, DUP[1]))),
        t("now remove the empty rows", brain(*EMP[:1], ("remove_empty_rows", "remove the empty rows")), plan((ER, "remove the empty rows")))]},
    "s12-again": {"file": "plain", "turns": [
        t("remove the duplicate rows", brain(*DUP[:1], DUP), plan((DD, DUP[1]))),
        t("remove the duplicate rows", brain(*DUP[:1], DUP), plan((DD, DUP[1])))]},
    "s13-password": {"file": "password", "turns": [t("remove the duplicate rows", brain(*DUP[:1], DUP), plan((DD, DUP[1])))]},
    "s14-nofile-dedupe": {"turns": [t("remove the duplicate rows", brain(*DUP[:1], DUP), plan((DD, DUP[1])))]},
    "s15-planner-raises": {"file": "plain", "turns": [t("remove the duplicate rows", brain(*DUP[:1], DUP), RuntimeError("boom gsk_SECRET"))]},
    "s16-email": {"file": "plain", "turns": [t(
        "remove the duplicate rows and email it to my boss",
        brain("multi_step_request", DUP, ("unsupported", "email it to my boss")), plan((DD, DUP[1])))]},
    "s17-hostile": {"file": "hostile_headers2", "turns": [t("remove the duplicate rows", brain(*DUP[:1], DUP), plan((DD, DUP[1])))]},
    "s18-formulas": {"file": "merged", "turns": [t("remove the duplicate rows", brain(*DUP[:1], DUP), plan((DD, DUP[1])))]},
    "s19-xls": {"file": "xls", "turns": [t("remove the empty rows", brain(*EMP[:1], EMP), plan((ER, EMP[1])))]},
    "s20-clarify": {"file": "plain", "turns": [t("clean it", brain("unclear"), None, t=think(True, clarification_reason="ambiguous"))]},
}
