"""Small wording helpers shared by the stages that write text a person reads."""


def count_noun(count: int, singular: str, plural: str | None = None) -> str:
    """``count`` with its noun in the right number: ``"1 row"``, ``"2 rows"``, ``"0 rows"``.

    Args:
        count: The number to show.
        singular: The noun for exactly one.
        plural: The noun for any other count. Defaults to ``singular`` + ``"s"``.
    """
    noun = singular if count == 1 else (plural if plural is not None else singular + "s")
    return f"{count} {noun}"
