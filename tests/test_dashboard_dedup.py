"""TASK-016: the dashboard builder applies the shared cross-dataset specimen dedup
(pipeline/shared.py specimen_key, TASK-014) to the counts it takes from jp — the SQL
aggregates (totals, decades, evidence, mesh) and the neighbour SQL's jp reads (leads):
one specimen twice-published under different gbifIDs counts once, the first in gbifID
order winning; an empty catalogNumber identifies nothing, so those rows each count.

The other dashboard suites run fixtures whose jp lacks the two columns, so they keep
proving the header guard side (every record kept, no SQL error) after this change.
"""
import json, os, re, sys, tempfile, unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import duckdb

from pipeline.geo_ja import cell_center  # noqa: E402
from test_public_taxa import FIX, write_coverage  # noqa: E402
from test_public_taxa_outputs import JP_COLUMNS, run_builder  # noqa: E402
from test_area_sanitization import FLAG_COLUMNS  # noqa: E402

NB_COLUMNS = "species varchar, phylum varchar, \"order\" varchar, family varchar, genus varchar, \
class varchar, numberOfOccurrences int, iucnRedListCategory varchar, taxonRank varchar, \
taxonomicStatus varchar, speciesKey varchar"


class CrossDatasetDeduplication(unittest.TestCase):
    def test_pair_counts_once_in_totals_mesh_and_leads(self):
        tmp = tempfile.mkdtemp()
        db = os.path.join(tmp, "xdup.duckdb")
        c = duckdb.connect(db)
        c.execute("create table jp(gbifID varchar, " + JP_COLUMNS + FLAG_COLUMNS
                  + ", institutionCode varchar, catalogNumber varchar)")
        c.execute("create table nb(" + NB_COLUMNS + ")")
        c.execute("create table mo(speciesKey varchar, countryCode varchar)")
        # one eligible cell (313:1021). Prosus rows "1"/"2" are one S-NET specimen
        # twice-published (different case/spacing, different datasetKey) — "1", first in
        # gbifID order, wins, so its 2019 survives and "2"'s 2021 never lands; "3"/"4"
        # carry no catalogNumber, so both count; "5"/"6" share pair "ZZ 9" across two
        # species, and "6" is Leadus candidus's only jp record — dropping it must leave
        # the neighbour SQL seeing that species as unrecorded in Japan.
        rows = [
            ("1", "5001", "Prosus lautus", "Prosus", "Fictaceae", "SPECIES", "HUMAN_OBSERVATION",
             "Agaricomycetes", "Basidiomycota", "26.1", "127.7", "3", "2019", None, "Okinawa",
             "dx", None, None, None, "S-NET", "AB 7"),
            ("2", "5001", "Prosus lautus", "Prosus", "Fictaceae", "SPECIES", "HUMAN_OBSERVATION",
             "Agaricomycetes", "Basidiomycota", "26.1", "127.7", "5", "2021", None, "Okinawa",
             "dy", None, None, None, " s-net ", "ab  7"),
            ("3", "5001", "Prosus lautus", "Prosus", "Fictaceae", "SPECIES", "HUMAN_OBSERVATION",
             "Agaricomycetes", "Basidiomycota", "26.1", "127.7", "7", "2020", None, "Okinawa",
             "dx", None, None, None, "S-NET", ""),
            ("4", "5001", "Prosus lautus", "Prosus", "Fictaceae", "SPECIES", "HUMAN_OBSERVATION",
             "Agaricomycetes", "Basidiomycota", "26.1", "127.7", "8", "2020", None, "Okinawa",
             "dx", None, None, None, "S-NET", None),
            ("5", "5001", "Prosus lautus", "Prosus", "Fictaceae", "SPECIES", "HUMAN_OBSERVATION",
             "Agaricomycetes", "Basidiomycota", "26.1", "127.7", "2", "2018", None, "Okinawa",
             "dx", None, None, None, "S-NET", "ZZ 9"),
            ("6", "5010", "Leadus candidus", "Leadus", "Fictaceae", "SPECIES", "HUMAN_OBSERVATION",
             "Agaricomycetes", "Basidiomycota", "26.1", "127.7", "1", "2017", None, "Okinawa",
             "dz", None, None, None, "S-NET", " zz 9 "),
        ]
        c.executemany("insert into jp values (" + ",".join(["?"] * 21) + ")", rows)
        c.execute("insert into nb values ('Leadus candidus','Basidiomycota','Agaricales','Fictaceae',"
                  "'Leadus','Agaricomycetes',77,NULL,'SPECIES','ACCEPTED','5010')")
        c.execute("insert into mo values ('5010','JP')")     # mo_records > 0: the lead publishes
        c.close()
        out = os.path.join(tmp, "dashboard.json")
        r = run_builder("build_dashboard_data.py", "--db", db, "--out", out,
                        "--mask", os.path.join(FIX, "taxon_mask.csv"),
                        "--coverage", write_coverage(tmp),
                        "--exceptions", os.path.join(FIX, "match_exceptions.csv"),
                        "--redlist", os.path.join(FIX, "redlist.json"))
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("cross-dataset duplicates removed: 2", r.stdout)
        with open(out) as f:
            d = json.load(f)
        # SQL aggregates: six raw rows -> four (each twice-published specimen once)
        self.assertEqual(d["totals"]["records"], 4)
        self.assertEqual(d["totals"]["datasets"], 1)          # "dy"/"dz" only ever reached via dupes
        self.assertEqual(d["evidence"], [["HUMAN_OBSERVATION", 4]])
        self.assertEqual(d["decades"], [[2010, 2], [2020, 2]])  # 2018+2019 / 2020+2020: survivor is
                                                               # gbifID "1"; "2"'s 2021 never lands
        # mesh: the one eligible cell holds the four deduped records, one species
        self.assertEqual(d["mesh"], [[*cell_center(313, 1021), 4, 1]])
        # leads: the neighbour SQL read the deduped jp — Leadus candidus's only jp record
        # was a dropped duplicate half, so it counts as unrecorded in Japan
        self.assertEqual(d["leads"], [["Leadus candidus", "Fictaceae", 77, 1, "genus unrecorded in Japan"]])
        self.assertEqual(d["lead_totals"], {"candidates": 1, "with_mo": 1,
                                            "with_unresolved_jp_genus": 0})


    def test_neighbour_sql_never_schema_qualifies_jp(self):
        # The dedup temp table shadows jp only for unqualified references (TASK-016):
        # a qualified main.jp (or any schema.jp) would silently read the raw, un-deduped
        # table and the leads would drift from the dashboard counts. Comments are
        # skipped so the header warning itself cannot trip the guard.
        with open(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                               "analysis", "01_neighbour_candidates.sql")) as f:
            code = "\n".join(ln for ln in f.read().splitlines()
                             if not ln.lstrip().startswith("--"))
        qualified = [f"{m}.{n}" for m, n in
                     re.findall(r"\b([A-Za-z_]\w*)\.([Jj][Pp])\b", code) if m.lower() != "temp"]
        self.assertEqual(qualified, [])


if __name__ == "__main__":
    unittest.main()
