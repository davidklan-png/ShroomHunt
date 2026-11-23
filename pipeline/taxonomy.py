"""Shared identification-depth buckets (TASK-021).

The national id_depth card (build_dashboard_data.py) and every per-area Overview
(build_areas.py) bucket taxonRank the same way through this module, so the two
can never drift. The species ranks are public_taxa.SPECIES_RANKS — the same set
the area builder already uses to split species rows from unresolved ones.
"""
import os, sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from public_taxa import SPECIES_RANKS  # noqa: E402


def id_depth_bucket(rank):
    """taxonRank -> 'species' | 'genus' | 'higher' (the id_depth bucket rule)."""
    if rank in SPECIES_RANKS: return "species"
    if rank == "GENUS": return "genus"
    return "higher"


def id_depth_sql(col="taxonRank"):
    """The same rule as a SQL case expression, for build_dashboard_data.py's aggregates."""
    ranks = ", ".join(f"'{r}'" for r in SPECIES_RANKS)
    return f"case when {col} in ({ranks}) then 'species' when {col}='GENUS' then 'genus' else 'higher' end"
