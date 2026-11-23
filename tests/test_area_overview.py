"""TASK-021: the per-area Overview aggregates — evidence, identification depth and
decades over exactly the record set totals.records counts (the gated, area-assigned
set: spatial and name-routed records alike, masked records never). Groups sum their
members' published arrays, as totals does; year-less records are left out of decades
and the gap is reported in the build log.

Fictional taxa and the TASK-005 fixture DB of test_area_sanitization.py.
"""
import json, os, sys, tempfile, unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import duckdb

from pipeline.taxonomy import id_depth_bucket, id_depth_sql  # noqa: E402
from test_public_taxa import FIX, write_coverage  # noqa: E402
from test_public_taxa_outputs import run_builder  # noqa: E402
from test_area_sanitization import (FLAG_COLUMNS, ISLANDS_CSV, JP_COLUMNS, JP_ROWS,  # noqa: E402
                                    XE_ROWS, EA_ROWS, build, read)


def make_db(path, rows):
    c = duckdb.connect(path)
    c.execute("create table jp(gbifID varchar, " + JP_COLUMNS + FLAG_COLUMNS + ")")
    c.executemany("insert into jp values (" + ",".join(["?"] * 19) + ")", rows)
    c.execute("create table jp_extra(gbifID varchar, habitat varchar, assoc varchar, elevation double, "
              "dataGeneralizations varchar, informationWithheld varchar)")
    c.executemany("insert into jp_extra values (?,?,?,?,?,?)", XE_ROWS)
    c.execute("create table ea(speciesKey varchar, species varchar, family varchar, "
              "lat double, lon double, month int, n int)")
    c.executemany("insert into ea values (?,?,?,?,?,?,?)", EA_ROWS)
    c.close()


def refs(tmp):
    return ["--mask", os.path.join(FIX, "taxon_mask.csv"),
            "--coverage", write_coverage(tmp),
            "--exceptions", os.path.join(FIX, "match_exceptions.csv"),
            "--redlist", os.path.join(FIX, "redlist.json")]


class SharedBuckets(unittest.TestCase):
    def test_national_sql_agrees_with_the_bucket_function(self):
        ranks = ["SPECIES", "VARIETY", "SUBSPECIES", "FORM", "GENUS", "FAMILY", "ORDER", None]
        c = duckdb.connect()
        c.execute("create table r(i int, taxonRank varchar)")
        c.executemany("insert into r values (?, ?)", list(enumerate(ranks)))
        sql_buckets = [x for (x,) in c.execute(f"select {id_depth_sql()} from r order by i").fetchall()]
        c.close()
        self.assertEqual(sql_buckets, [id_depth_bucket(r) for r in ranks])


class OverviewAggregates(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp()
        cls.islands = os.path.join(cls.tmp, "islands.csv")
        with open(cls.islands, "w") as f:
            f.write(ISLANDS_CSV)
        cls.refs = refs(cls.tmp)
        cls.db = os.path.join(cls.tmp, "agg.duckdb")
        make_db(cls.db, JP_ROWS)
        cls.areas = build(cls.db, cls.islands, cls.refs)
        cls.files = sorted(n for n in os.listdir(cls.areas) if n != "index.json")
        cls.docs = {n: json.loads(read(os.path.join(cls.areas, n))) for n in cls.files}
        with open(os.path.join(cls.areas, "index.json")) as f:
            cls.index = json.load(f)

    def test_okisle_counts_every_assigned_record(self):
        # 10 records: 9 HUMAN_OBSERVATION + 1 PRESERVED_SPECIMEN; 9 species-rank + the
        # Cleanus GENUS row; years 2009 x1, 2013-2019 x7, 2020-2021 x2 — the name-only
        # Nominis row (2013) included, the dropped Ghostus row nowhere.
        ok = self.docs["okisle.json"]
        self.assertEqual(ok["totals"]["records"], 10)
        self.assertEqual(ok["evidence"], [["HUMAN_OBSERVATION", 9], ["PRESERVED_SPECIMEN", 1]])
        self.assertEqual(ok["id_depth"], [["species", 9], ["genus", 1], ["higher", 0]])
        self.assertEqual(ok["decades"], [[2000, 1], [2010, 7], [2020, 2]])

    def test_every_area_sums_to_totals_records(self):
        for name, doc in self.docs.items():
            n = doc["totals"]["records"]
            self.assertEqual(sum(r[1] for r in doc["evidence"]), n, name)
            self.assertEqual([r[0] for r in doc["id_depth"]], ["species", "genus", "higher"], name)
            self.assertEqual(sum(r[1] for r in doc["id_depth"]), n, name)
            self.assertLessEqual(sum(r[1] for r in doc["decades"]), n, name)
            self.assertEqual([r[0] for r in doc["decades"]], sorted(r[0] for r in doc["decades"]), name)
            self.assertEqual([r[1] for r in doc["evidence"]],
                             sorted((r[1] for r in doc["evidence"]), reverse=True), name)

    def test_group_sums_the_members_arrays(self):
        group = self.docs["g-okinawa-prefecture.json"]
        self.assertEqual(group["members"], ["okisle", "lilisle"])
        # independent merge of the two member files, then the exact expected values
        for field in ("evidence", "id_depth", "decades"):
            merged = {}
            for m in group["members"]:
                for k, n in self.docs[m + ".json"][field]:
                    merged[k] = merged.get(k, 0) + n
            self.assertEqual(dict(group[field]), merged, field)
        self.assertEqual(group["evidence"], [["HUMAN_OBSERVATION", 10], ["PRESERVED_SPECIMEN", 1]])
        self.assertEqual(group["id_depth"], [["species", 10], ["genus", 1], ["higher", 0]])
        self.assertEqual(group["decades"], [[2000, 1], [2010, 8], [2020, 2]])

    def test_masked_record_changes_no_aggregate(self):
        # one extra masked-species record inside okisle's cell: masked_records rises,
        # and not one published byte moves — the three arrays included
        masked_row = ("99", None, "Tricholoma fictum", "Tricholoma", "Fictaceae", "SPECIES",
                      "MATERIAL_SAMPLE", "Agaricomycetes", "Basidiomycota", "26.1", "127.7",
                      "4", "1990", None, "Okinawa", "dx", None, None, None)
        db = os.path.join(self.tmp, "masked.duckdb")
        make_db(db, JP_ROWS + [masked_row])
        out = build(db, self.islands, self.refs)
        for name in self.files:
            self.assertEqual(read(os.path.join(out, name)), read(os.path.join(self.areas, name)), name)
        with open(os.path.join(out, "index.json")) as f:
            self.assertEqual(json.load(f)["masked_records"], self.index["masked_records"] + 1)


class YearlessRecords(unittest.TestCase):
    def test_records_without_usable_year_are_left_out_and_reported(self):
        # okisle cell 313:1021 — one 1995 record binned, a null year and two outside
        # the 1850-2100 national window left out, and a null basisOfRecord sorting last
        rows = [
            ("y1", "5201", "Datus annus", "Datus", "Fictaceae", "SPECIES", "HUMAN_OBSERVATION",
             "Agaricomycetes", "Basidiomycota", "26.1", "127.7", "3", "1995", None, "Okinawa",
             "dx", None, None, None),
            ("y2", "5201", "Datus annus", "Datus", "Fictaceae", "SPECIES", "HUMAN_OBSERVATION",
             "Agaricomycetes", "Basidiomycota", "26.1", "127.7", "4", None, None, "Okinawa",
             "dx", None, None, None),
            ("y3", "5201", "Datus annus", "Datus", "Fictaceae", "SPECIES", "HUMAN_OBSERVATION",
             "Agaricomycetes", "Basidiomycota", "26.1", "127.7", "5", "1800", None, "Okinawa",
             "dx", None, None, None),
            ("y4", "5201", "Datus annus", "Datus", "Fictaceae", "SPECIES", "HUMAN_OBSERVATION",
             "Agaricomycetes", "Basidiomycota", "26.1", "127.7", "6", "2150", None, "Okinawa",
             "dx", None, None, None),
            ("y5", "5202", "Vacuus basis", "Vacuus", "Fictaceae", "SPECIES", None,
             "Agaricomycetes", "Basidiomycota", "26.1", "127.7", "7", "1995", None, "Okinawa",
             "dx", None, None, None),
        ]
        tmp = tempfile.mkdtemp()
        db = os.path.join(tmp, "yr.duckdb")
        make_db(db, rows)
        islands = os.path.join(tmp, "islands.csv")
        with open(islands, "w") as f:
            f.write(ISLANDS_CSV)
        out = tempfile.mkdtemp(prefix="areas-")
        r = run_builder("build_areas.py", "--db", db, "--out", out, "--islands", islands, *refs(tmp))
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("records left out of decades (no usable year): 3", r.stdout)
        with open(os.path.join(out, "okisle.json")) as f:
            ok = json.load(f)
        self.assertEqual(ok["totals"]["records"], 5)                  # every record still counted
        self.assertEqual(ok["decades"], [[1990, 2]])                  # only the two 1995 rows
        self.assertEqual(ok["evidence"], [["HUMAN_OBSERVATION", 4], [None, 1]])
        self.assertEqual(sum(x[1] for x in ok["id_depth"]), 5)


if __name__ == "__main__":
    unittest.main()
