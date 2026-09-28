"""Spike: real output from ydata-profiling and Splink on a messy spreadsheet.

Throwaway exploration script  -  NOT part of the buddy/ pipeline, NOT wired
into skills/, NOT covered by the test suite. It exists purely so we can
look at what these two tools actually say before deciding how (or
whether) to integrate either one. Safe to delete once that decision is
made; nothing under buddy/ or skills/ imports this file.

What it does:
    1. Builds a realistic messy spreadsheet in memory (missing values, a
       couple of numeric outliers, an inconsistent date column, and a set
       of near-duplicate rows where similar names are deliberately paired
       with a phone-number column that sometimes matches and sometimes
       doesn't), writes it to .xlsx, then reads it back  -  so both tools
       see exactly what a real uploaded file would look like, not the
       in-memory DataFrame directly.
    2. Runs ydata-profiling on it and reports what it actually flagged.
    3. Runs Splink in dedupe_only mode on the same data and reports what
       it identified as likely duplicates, including a specific check of
       whether the phone-number column stopped it from merging two
       different people who happen to share a very similar name.

Usage:
    python scripts/spike_tools_test.py

Output lands in scratch/spike_tools/ (gitignored  -  see .gitignore's
"Manual verification scratch space" entry): the messy fixture as .xlsx,
the full ydata-profiling HTML report, and a text summary of both tools'
findings printed to stdout.

Requires ydata-profiling and splink  -  see requirements.txt's "SPIKE ONLY"
section. Neither is a real project dependency yet.
"""

import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

SCRATCH_DIR = Path(__file__).resolve().parent.parent / "scratch" / "spike_tools"

# (unique_id, name, phone, email, amount, signup_date)
#
# Three "groups" of interest, by design:
#   - True-duplicate pairs: same real person, a spelling/nickname variant
#     of their name, but the SAME phone number (ids 1/2, 3/4, 5/6).
#   - Name-collision singles: a DIFFERENT real person with the exact same
#     name text as one of the pairs above, but a DIFFERENT phone number
#     (ids 7, 8, 9)  -  the case that should NOT be merged.
#   - Unrelated singles with their own messiness: missing phone/email/
#     amount, two numeric outliers in `amount` (ids 11, 24), and signup
#     dates written in a handful of inconsistent formats including one
#     outright unparseable string (id 15).
_MESSY_ROWS: list[tuple[int, str, object, object, object, str]] = [
    (1, "Alice Johnson", "555-0101", "alice.johnson@email.com", 120.50, "2024-01-05"),
    (2, "Alice Johnston", "555-0101", "alice.johnson@email.com", 118.00, "01/05/2024"),
    (3, "Robert Smith", "555-0202", "bob.smith@email.com", 75.25, "2024-01-07"),
    (4, "Bob Smith", "555-0202", "bob.smith@email.com", 76.00, "Jan 7, 2024"),
    (5, "Carol Davis", "555-0303", "carol.davis@email.com", 200.00, "2024-01-09"),
    (6, "Carol Davies", "555-0303", None, 205.50, "09/01/2024"),
    (7, "Alice Johnson", "555-0199", "alice.j99@email.com", 95.00, "2024-01-06"),
    (8, "Robert Smith", "555-0288", "rsmith288@email.com", 60.00, "2024/01/08"),
    (9, "Carol Davis", "555-0377", "cdavis377@email.com", 88.00, "2024-01-11"),
    (10, "Frank Nguyen", "555-0505", "frank.n@email.com", 45.00, "2024-01-12"),
    (11, "Grace Kim", "555-0606", None, 999999.99, "2024-01-13"),
    (12, "Henry Patel", None, "henry.patel@email.com", 30.00, "13-01-2024"),
    (13, "Irene Chao", "555-0808", "irene.chao@email.com", None, "2024-01-14"),
    (14, "Jack Turner", "555-0909", "jack.turner@email.com", 62.50, "not a date"),
    (15, "Karen Wu", "555-1010", "karen.wu@email.com", 58.00, "2024-01-15"),
    (16, "Liam O'Brien", "555-1111", "liam.obrien@email.com", 54.00, "2024-01-16"),
    (17, "Maria Garcia", "555-1212", "maria.garcia@email.com", 40.00, "16/01/2024"),
    (18, "Noah Lee", "555-1313", "noah.lee@email.com", 52.00, "2024-01-17"),
    (19, "Olivia Brown", "555-1414", None, 47.00, "Jan 18 2024"),
    (20, "Peter Chen", "555-1515", "peter.chen@email.com", None, "2024-01-19"),
    (21, "Quinn Adams", "555-1616", "quinn.adams@email.com", 63.00, "19/01/2024"),
    (22, "Rachel Kim", "555-1717", "rachel.kim@email.com", 71.00, "2024-01-20"),
    (23, "Sam Park", "555-1818", "sam.park@email.com", 500000.00, "2024-01-21"),
    (24, "Tina Ali", "555-1919", "tina.ali@email.com", 39.00, "2024-01-22"),
]

# The specific pairs the Splink section checks by hand, by unique_id.
_TRUE_DUPLICATE_PAIRS = [(1, 2, "Alice Johnson/Johnston, same phone"),
                         (3, 4, "Robert/Bob Smith, same phone"),
                         (5, 6, "Carol Davis/Davies, same phone")]
_NAME_COLLISION_PAIRS = [(1, 7, "two 'Alice Johnson's, DIFFERENT phone"),
                          (3, 8, "two 'Robert Smith's, DIFFERENT phone"),
                          (5, 9, "two 'Carol Davis's, DIFFERENT phone")]


def _build_messy_dataframe() -> pd.DataFrame:
    return pd.DataFrame(
        _MESSY_ROWS,
        columns=["unique_id", "name", "phone", "email", "amount", "signup_date"],
    )


def _write_and_reread_fixture(df: pd.DataFrame) -> pd.DataFrame:
    """Round-trip through a real .xlsx file, the way an uploaded
    spreadsheet actually reaches WorkBuddy, rather than handing either
    tool the in-memory DataFrame directly.
    """
    SCRATCH_DIR.mkdir(parents=True, exist_ok=True)
    fixture_path = SCRATCH_DIR / "messy_report.xlsx"
    df.to_excel(fixture_path, index=False)
    print(f"Wrote messy fixture to: {fixture_path}")
    return pd.read_excel(fixture_path)


def _print_header(title: str) -> None:
    print()
    print(f"=== {title} ===")


def run_profiling(df: pd.DataFrame) -> None:
    from ydata_profiling import ProfileReport

    _print_header("ydata-profiling")

    report = ProfileReport(
        df,
        title="WorkBuddy spike  -  messy spreadsheet profile",
        progress_bar=False,
    )
    html_path = SCRATCH_DIR / "profile_report.html"
    report.to_file(html_path)
    print(f"Full HTML report written to: {html_path}")

    description = report.get_description()
    print(f"\n{len(description.alerts)} alert(s) raised:")
    for alert in description.alerts:
        print(f"  - {alert.alert_type.name}: {alert}")

    print("\nMissing values by column (for comparison against the alerts above):")
    print(df.isna().sum().to_string())

    print("\namount column stats (the two outliers are ids 11 and 23):")
    print(df["amount"].describe().to_string())

    print("\nsignup_date column's distinct raw values (never parsed  -  this is")
    print("the inconsistent-format column):")
    for value in df["signup_date"].unique():
        print(f"  {value!r}")


def run_splink(df: pd.DataFrame) -> None:
    from splink import DuckDBAPI, Linker, SettingsCreator, block_on
    import splink.comparison_library as cl

    _print_header("Splink (dedupe_only)")

    splink_df = df[["unique_id", "name", "phone", "email"]].copy()

    settings = SettingsCreator(
        link_type="dedupe_only",
        comparisons=[cl.NameComparison("name"), cl.ExactMatch("phone")],
        blocking_rules_to_generate_predictions=[
            block_on("substr(name, 1, 3)"),
            block_on("phone"),
        ],
        additional_columns_to_retain=["email"],
    )
    linker = Linker(splink_df, settings, db_api=DuckDBAPI())

    linker.training.estimate_u_using_random_sampling(max_pairs=1e5)
    # Two EM passes with complementary blocking rules, so each comparison
    # column gets to be the "held out" one the other pass trains against
    # (a column can't have its own m-probability estimated in the same
    # pass where it's also the blocking key  -  see Splink's own warning
    # about this, reproduced by an earlier single-pass attempt here).
    linker.training.estimate_parameters_using_expectation_maximisation(
        block_on("substr(name, 1, 3)")
    )
    linker.training.estimate_parameters_using_expectation_maximisation(block_on("phone"))

    predictions = linker.inference.predict()
    pairs = predictions.as_pandas_dataframe().sort_values(
        "match_probability", ascending=False
    )

    print(f"\n{len(pairs)} candidate pair(s) generated by blocking. All of them,")
    print("by descending match_probability:")
    for _, row in pairs.iterrows():
        print(
            f"  [{row['unique_id_l']:>2} vs {row['unique_id_r']:>2}] "
            f"{row['name_l']!r} / {row['name_r']!r}  "
            f"phone {row['phone_l']!r} vs {row['phone_r']!r}  "
            f"-> match_probability={row['match_probability']:.4f} "
            f"(match_weight={row['match_weight']:.2f})"
        )

    def _lookup(id_a: int, id_b: int) -> float | None:
        match = pairs[
            ((pairs["unique_id_l"] == id_a) & (pairs["unique_id_r"] == id_b))
            | ((pairs["unique_id_l"] == id_b) & (pairs["unique_id_r"] == id_a))
        ]
        if match.empty:
            return None
        return float(match.iloc[0]["match_probability"])

    print("\n--- True duplicates (same phone)  -  expect HIGH match_probability ---")
    for id_a, id_b, note in _TRUE_DUPLICATE_PAIRS:
        prob = _lookup(id_a, id_b)
        shown = f"{prob:.4f}" if prob is not None else "NOT GENERATED as a candidate pair"
        print(f"  ids {id_a}/{id_b} ({note}): {shown}")

    print("\n--- Name collisions (different phone)  -  expect LOW match_probability ---")
    print("(this is the specific check: did Splink use the phone column to avoid")
    print("merging two different people who share a name?)")
    for id_a, id_b, note in _NAME_COLLISION_PAIRS:
        prob = _lookup(id_a, id_b)
        shown = f"{prob:.4f}" if prob is not None else "NOT GENERATED as a candidate pair"
        print(f"  ids {id_a}/{id_b} ({note}): {shown}")


def main() -> None:
    df = _build_messy_dataframe()
    df = _write_and_reread_fixture(df)

    run_profiling(df)
    run_splink(df)

    print()
    print(f"Done. All output files are under: {SCRATCH_DIR}")


if __name__ == "__main__":
    main()
