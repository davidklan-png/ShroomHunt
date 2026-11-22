#!/usr/bin/env python3
"""Shared protected-taxon gate for every public output (plan §7, TASK-002).

One decision — "may this taxon appear publicly?" — feeds the dashboard mesh,
family lists, leads, unresolved genera, area species/candidate tables and the
neighbour SQL. Builders keep no local predicates.

Reviewed inputs (data/reference/, see also the seed files there):
  mask       taxon_mask.csv        commercial taxa: accepted name, pipe-separated aliases
  coverage   redlist_coverage.csv  pins the reviewed Red List download (sha256 + counts)
  exceptions match_exceptions.csv  reviewer overrides: alias (mask too) / ignore (never mask)
  redlist    data/raw/redlist/redlist_fungi_jp.json (pipeline/fetch_redlist.py)

Fail-closed: a missing mask, missing/empty/mismatched coverage manifest or
missing Red List download raises PublicTaxaError before anything is written.

A record is masked when its taxon key, or its accepted/alias/verbatim name,
resolves to a masked taxon (Red List names count, including infraspecific
prefixes). Red List entries matched fuzzily by fetch_redlist.py are treated as
ambiguous and quarantined by name. Records not identified to species rank in a
masked genus are quarantined: the finer identity cannot rule out a protected
taxon. Audit output is aggregate counts only, never per-taxon names.

Regenerate the coverage row after a fresh Red List download:
  python3 pipeline/public_taxa.py data/raw/redlist/redlist_fungi_jp.json >> data/reference/redlist_coverage.csv
then have it reviewed before publishing.
"""
import csv, hashlib, json, os, sys

SPECIES_RANKS = ("SPECIES", "VARIETY", "SUBSPECIES", "FORM")


class PublicTaxaError(Exception):
    """Publish-blocking problem with the protected-taxon reference inputs."""


def _fold(name):
    return (name or "").strip().lower()


def _read_csv(path, what):
    """Comment-prefixed ('#') CSV of reference rows; [] when only comments remain."""
    try:
        with open(path, newline="", encoding="utf-8") as f:
            lines = [ln for ln in f if not ln.lstrip().startswith("#")]
    except FileNotFoundError:
        raise PublicTaxaError(f"{what} missing: {path}")
    return list(csv.DictReader(lines))


def redlist_facts(path):
    """(sha256, n_names, n_matched, n_ambiguous, species list) for a Red List download."""
    try:
        with open(path, "rb") as f:
            raw = f.read()
    except FileNotFoundError:
        raise PublicTaxaError(f"Red List download missing: {path}")
    d = json.loads(raw)
    species = d["species"]
    matched = sum(1 for s in species if s.get("speciesKey"))
    ambiguous = sum(1 for s in species if s.get("speciesKey") and s.get("matchType") not in (None, "EXACT"))
    # Digest covers stable content only: the file's "fetched" date changes on every
    # re-download, so hashing raw bytes would break the coverage row across runs.
    canon = json.dumps({"dataset": d.get("dataset"), "doi": d.get("doi"), "species": species},
                       sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode()
    return hashlib.sha256(canon).hexdigest(), len(species), matched, ambiguous, species


class Gate:
    def __init__(self):
        self.masked_names = set()      # accepted + aliases + exact Red List names (folded)
        self.ambiguous_names = set()   # fuzzy-matched Red List names (folded)
        self.keys = set()              # masked taxon keys as strings
        self._binomials = {}           # genus -> {binomial names} for infraspecific prefix match
        self.redlist_names = 0         # aggregate count for dashboard totals

    def _add_name(self, name, ambiguous=False):
        n = _fold(name)
        if not n:
            return
        (self.ambiguous_names if ambiguous else self.masked_names).add(n)
        if " " in n:
            self._binomials.setdefault(n.split()[0], set()).add(n)

    def _drop_name(self, name):
        n = _fold(name)
        self.masked_names.discard(n)
        self.ambiguous_names.discard(n)

    def _rebuild_derived(self):
        """Recompute the prefix sets from the surviving names, so an ignored name
        no longer masks its infraspecific forms (unless another masked name shares them)."""
        self._binomials = {}
        for n in self.masked_names | self.ambiguous_names:
            if " " in n:
                self._binomials.setdefault(n.split()[0], set()).add(n)

    def classify(self, species=None, species_key=None, genus=None, taxon_rank=None):
        """Why this record is masked: 'key', 'name', 'ambiguous', or None if public.

        Species-level masking only (David's ruling, 2026-11-22): a record masks when
        it IS the protected taxon (key, name/alias, ambiguous match, or an
        infraspecific form of a masked binomial). Genus-rank records publish —
        masking a whole genus for one protected species was rejected."""
        if species_key is not None and str(species_key) in self.keys:
            return "key"
        n = _fold(species)
        if n:
            if n in self.masked_names:
                return "name"
            if n in self.ambiguous_names:
                return "ambiguous"
            g = _fold(genus) or n.split()[0]
            if any(n.startswith(b + " ") for b in self._binomials.get(g, ())):
                return "name"  # infraspecific/null-key form of a masked binomial
        return None

    def is_public(self, species=None, species_key=None, genus=None, taxon_rank=None):
        return self.classify(species, species_key, genus, taxon_rank) is None

    def audit(self, rows):
        """Aggregate counts for (species, key, genus, rank) tuples. No taxon names."""
        counts = {"public": 0, "masked_key": 0, "masked_name": 0, "quarantined_ambiguous": 0}
        for species, key, genus, rank in rows:
            reason = self.classify(species, key, genus, rank)
            counts["public" if reason is None else
                   {"key": "masked_key", "name": "masked_name",
                    "ambiguous": "quarantined_ambiguous"}[reason]] += 1
        return counts

    def install_sql(self, con):
        """Temp tables backing sql_where(); run before analysis/01_neighbour_candidates.sql."""
        con.execute("create or replace temp table sens(k varchar)")
        con.executemany("insert into sens values (?)", [(k,) for k in sorted(self.keys)])
        con.execute("create or replace temp table sens_names(name varchar)")
        names = sorted(self.masked_names | self.ambiguous_names)
        con.executemany("insert into sens_names values (?)", [(n,) for n in names])
        con.execute("create or replace temp table sens_prefix(name varchar)")
        prefixes = sorted(n for bs in self._binomials.values() for n in bs)
        con.executemany("insert into sens_prefix values (?)", [(p,) for p in prefixes])

    def sql_where(self, t="jp"):
        """SQL predicate over a table/alias with speciesKey/species/genus/taxonRank columns."""
        return (f"(coalesce({t}.speciesKey::varchar, '') not in (select k from sens)"
                f" and lower(coalesce({t}.species, '')) not in (select name from sens_names)"
                f" and not exists (select 1 from sens_prefix p"
                f"                 where lower(coalesce({t}.species, '')) like p.name || ' %'))")  # noqa: F541


def load_gate(mask="data/reference/taxon_mask.csv", coverage="data/reference/redlist_coverage.csv",
              exceptions="data/reference/match_exceptions.csv",
              redlist="data/raw/redlist/redlist_fungi_jp.json"):
    """Build the gate from reviewed inputs; raises PublicTaxaError on anything absent or partial."""
    mask_rows = _read_csv(mask, "commercial taxon mask")
    mask_rows = [r for r in mask_rows if r.get("scientific_name", "").strip()]
    if not mask_rows:
        raise PublicTaxaError(f"commercial taxon mask has no taxa: {mask}")

    sha, n_names, n_matched, n_ambiguous, species = redlist_facts(redlist)
    cov = _read_csv(coverage, "Red List coverage manifest")
    if not cov:
        raise PublicTaxaError(f"Red List coverage manifest is empty (unreviewed seed?): {coverage}")
    expected = {"sha256": sha, "names": str(n_names), "matched": str(n_matched), "ambiguous": str(n_ambiguous)}
    # Rows are appended after each reviewed download (see __main__); any full match covers this one.
    if not any(all((row.get(f) or "").strip().lower() == v.lower() for f, v in expected.items()) for row in cov):
        raise PublicTaxaError(
            f"Red List coverage manifest has no row matching the download "
            f"({', '.join(f'{k}={v!r}' for k, v in expected.items())}); "
            f"partial or stale coverage blocks publishing: {coverage}")

    g = Gate()
    g.redlist_names = n_names
    for r in mask_rows:
        g._add_name(r["scientific_name"])
        for alias in (r.get("aliases") or "").split("|"):
            if alias.strip():
                g._add_name(alias)
    for s in species:
        if s.get("speciesKey"):
            g.keys.add(str(s["speciesKey"]))
        g._add_name(s["name"], ambiguous=bool(s.get("speciesKey")) and s.get("matchType") not in (None, "EXACT"))
    for r in _read_csv(exceptions, "match-exception manifest"):
        name, action = (r.get("name") or "").strip(), (r.get("action") or "").strip().lower()
        if not name:
            continue
        if action == "alias":
            g._add_name(name)
        elif action == "ignore":
            g._drop_name(name)
            g.keys -= {str(s["speciesKey"]) for s in species if _fold(s["name"]) == _fold(name)}
        else:
            raise PublicTaxaError(f"match-exception manifest has unknown action {action!r} for {name!r}")
    g._rebuild_derived()
    return g


def coverage_row(redlist_path):
    """The manifest line to append after a fresh download, for review."""
    sha, n_names, n_matched, n_ambiguous, _ = redlist_facts(redlist_path)
    return {"sha256": sha, "names": str(n_names), "matched": str(n_matched), "ambiguous": str(n_ambiguous)}


if __name__ == "__main__":
    if len(sys.argv) != 2:
        sys.exit("usage: python3 pipeline/public_taxa.py <redlist_fungi_jp.json>  # prints a coverage manifest row")
    row = coverage_row(sys.argv[1])
    print(",".join(row.values()))
