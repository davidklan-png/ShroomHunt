"""Shared source-precision eligibility for every spatial output (§7).

Moved out of build_areas.py (TASK-005) in TASK-006 so the dashboard mesh and
the area index apply one rule through one function: a record joins the
spatial subset only when nothing says its coordinates are wrong or coarser
than one 2nd-mesh cell. A cell holding only ineligible records can then
appear in neither dashboard.json nor the index, never in just one of them.

Each check applies only when the loaded tables actually carry the column (a
missing column is skipped, never guessed):
  jp.coordinateUncertaintyInMeters > CELL_MAX_UNCERTAINTY_M (5' of latitude, the cell's
      largest dimension: the true point can leave its own cell)
  jp.coordinatePrecision > CELL_MAX_PRECISION (coarser than the cell's latitude unit)
  jp.issue containing a flag from BAD_ISSUES (broken or relocated coordinates)
  jp_extra.dataGeneralizations / informationWithheld (DwC-A only): any non-empty value
      excludes the record — what exactly was generalized or withheld cannot be checked,
      so this fails closed.
"""
import os, sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from geo_ja import MESH_LAT_CELLS  # noqa: E402

CELL_MAX_UNCERTAINTY_M = 9260        # metres: 5 minutes of latitude
CELL_MAX_PRECISION = 1 / MESH_LAT_CELLS
BAD_ISSUES = {"ZERO_COORDINATE", "COORDINATE_INVALID", "COORDINATE_OUT_OF_RANGE",
              "COORDINATE_UNCERTAINTY_METERS_INVALID", "PRESUMED_SWAPPED_COORDINATE",
              "PRESUMED_NEGATED_LATITUDE", "PRESUMED_NEGATED_LONGITUDE"}


def spatially_eligible(unc, prec, issues, generalized, withheld):
    """May this record's coordinates place it on a public JIS cell?"""
    if unc is not None and unc > CELL_MAX_UNCERTAINTY_M: return False
    if prec is not None and prec > CELL_MAX_PRECISION: return False
    if issues and BAD_ISSUES & {s.strip() for s in issues.split(";")}: return False
    return not ((generalized and generalized.strip()) or (withheld and withheld.strip()))


def precision_selection(con, jp="jp", extra="jp_extra"):
    """(join clause, five SELECT expressions) for the eligibility columns, aware of
    which columns the loaded tables actually carry."""
    tables = {r[0] for r in con.execute("select table_name from information_schema.tables").fetchall()}

    def cols(table):
        return {r[0] for r in con.execute(
            f"select column_name from information_schema.columns where table_name='{table}'").fetchall()}

    jp_cols = cols(jp)
    xe_cols = cols(extra) if extra in tables else set()
    elig_join = (f"left join {extra} x on x.gbifID = {jp}.gbifID"
                 if "gbifID" in jp_cols and {"dataGeneralizations", "informationWithheld"} & xe_cols else "")
    sel = [f"try_cast({jp}.coordinateUncertaintyInMeters as double)" if "coordinateUncertaintyInMeters" in jp_cols else "null",
           f"try_cast({jp}.coordinatePrecision as double)" if "coordinatePrecision" in jp_cols else "null",
           f"{jp}.issue" if "issue" in jp_cols else "null",
           *([f"x.{col}" if col in xe_cols else "null" for col in ("dataGeneralizations", "informationWithheld")]
             if elig_join else ["null", "null"])]
    return elig_join, sel
