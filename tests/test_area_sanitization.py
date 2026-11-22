"""TASK-005: sanitized public area detail — no occurrence-derived prose, spatial
outputs from eligible whole cells only, candidate distances as 50 km bands from
public-resolution geography (snapped cell centroids and a fixed public area
centre; East Asia keeps its native 1° cells).

Fictional taxa only, as in test_public_taxa_outputs.py. The fixture DB carries
the GBIF SIMPLE_CSV precision columns plus a DwC-A jp_extra with the withheld/
generalized notes, so every eligibility branch is exercised.
"""
import json, os, sys, tempfile, unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import duckdb

from pipeline.geo_ja import cell_center, haversine_km, mesh_cell  # noqa: E402
from test_public_taxa import FIX, write_coverage  # noqa: E402
from test_public_taxa_outputs import JP_COLUMNS, run_builder  # noqa: E402

BAND = lambda d: 50 * min(5, int(d // 50))   # §6: lower edge of a 50 km band, capped at 250

ISLANDS_CSV = """id,name_en,name_ja,group_en,group_ja,lat_min,lat_max,lon_min,lon_max,prefecture,aliases
okisle,Oki Island,沖島,Oki Islands,沖諸島,26.0,26.6,127.5,128.0,Okinawa,oki isle|沖島
lilisle,Lil Island,小島,Oki Islands,沖諸島,26.4,26.8,127.7,128.2,Okinawa,lil isle|小島
"""

FLAG_COLUMNS = ", coordinateUncertaintyInMeters varchar, coordinatePrecision varchar, issue varchar"

# (gbifID, speciesKey, species, genus, family, rank, basis, class, phylum,
#  lat, lon, month, year, locality, pref, dataset, uncertainty, precision, issue)
JP_ROWS = [
    # eligible singleton with prose in every withdrawn field; exact SW corner of cell 313:1021
    ("1", "5001", "Prosus lautus", "Prosus", "Fictaceae", "SPECIES", "HUMAN_OBSERVATION", "Agaricomycetes",
     "Basidiomycota", "26.083333333333332", "127.625", "3", "2019", "Hidden Corner Meadow", "Okinawa",
     "dx", None, None, None),
    # same cell as above: two records, still exactly one whole square
    ("2", "5001", "Prosus lautus", "Prosus", "Fictaceae", "SPECIES", "PRESERVED_SPECIMEN", "Agaricomycetes",
     "Basidiomycota", "26.1", "127.7", "5", "2021", None, "Okinawa", "dx", None, None, None),
    # flagged sources: valid coordinates that must never reach a spatial output
    ("3", "5002", "Uncerus vagus", "Uncerus", "Fictaceae", "SPECIES", "HUMAN_OBSERVATION", "Agaricomycetes",
     "Basidiomycota", "26.3", "127.7", "2", "2018", "Oki Isle", "Okinawa", "dx", "25000", None, None),
    ("4", "5003", "Coarsus latus", "Coarsus", "Fictaceae", "SPECIES", "HUMAN_OBSERVATION", "Agaricomycetes",
     "Basidiomycota", "26.5", "127.75", "8", "2017", "Oki Isle", "Okinawa", "dx", None, "0.5", None),
    ("5", "5004", "Flagellus corruptus", "Flagellus", "Fictaceae", "SPECIES", "HUMAN_OBSERVATION", "Agaricomycetes",
     "Basidiomycota", "26.5", "127.9", "1", "2016", "Oki Isle", "Okinawa", "dx", None, None,
     "COORDINATE_ROUNDED;PRESUMED_SWAPPED_COORDINATE"),
    # benign flags keep the record spatial: common GBIF notices are not exclusions
    ("6", "5005", "Sanus communis", "Sanus", "Fictaceae", "SPECIES", "HUMAN_OBSERVATION", "Agaricomycetes",
     "Basidiomycota", "26.45", "127.85", "7", "2020", "Naha Ward", "Okinawa", "dx", "9000", None,
     "GEODETIC_DATUM_ASSUMED_WGS84;COORDINATE_ROUNDED"),
    # DwC-A withheld/generalized notes (jp_extra): excluded, fail closed
    ("7", "5006", "Obscurus celatus", "Obscurus", "Fictaceae", "SPECIES", "HUMAN_OBSERVATION", "Agaricomycetes",
     "Basidiomycota", "26.2", "127.6", "9", "2015", "Oki Isle", "Okinawa", "dx", None, None, None),
    ("8", "5007", "Generalizus latus", "Generalizus", "Fictaceae", "SPECIES", "HUMAN_OBSERVATION", "Agaricomycetes",
     "Basidiomycota", "26.2", "127.65", "4", "2014", "Oki Isle", "Okinawa", "dx", None, None, None),
    # name-only: no coordinates at all
    ("9", "5008", "Nominis tantum", "Nominis", "Fictaceae", "SPECIES", "HUMAN_OBSERVATION", "Agaricomycetes",
     "Basidiomycota", None, None, "5", "2013", "Oki Isle", "Okinawa", "dx", None, None, None),
    # eligible, but in the 1st-mesh tile (no island claims the cell centre): a JP candidate source
    ("10", "5009", "Neighborus proximus", "Neighborus", "Fictaceae", "SPECIES", "HUMAN_OBSERVATION", "Agaricomycetes",
     "Basidiomycota", "26.9", "128.15", "6", "2012", None, "Okinawa", "dx", None, None, None),
    # flagged with no matching locality: dropped from every area, and never a candidate source
    ("11", "5010", "Ghostus absentis", "Ghostus", "Fictaceae", "SPECIES", "HUMAN_OBSERVATION", "Agaricomycetes",
     "Basidiomycota", "27.0", "128.3", "10", "2011", None, "Okinawa", "dx", "30000", None, None),
    # flagged but name-assignable: counts in lilisle totals, never in cells or candidates
    ("12", "5012", "Phantomus lilii", "Phantomus", "Fictaceae", "SPECIES", "HUMAN_OBSERVATION", "Agaricomycetes",
     "Basidiomycota", "26.6", "128.0", "11", "2010", "Lil Isle", "Okinawa", "dx", None, "1.0", None),
    # eligible genus-only row (public genus): spatial, but unresolved in the species table
    ("13", None, None, "Cleanus", "Fictaceae", "GENUS", "HUMAN_OBSERVATION", "Agaricomycetes",
     "Basidiomycota", "26.15", "127.65", "12", "2009", None, "Okinawa", "dx", None, None, None),
]
XE_ROWS = [("1", "on decaying Pinus trunk", "with Castanopsis sieboldii", 1234.0, None, None),
           ("7", None, None, None, None, "location obscured for conservation"),
           ("8", None, None, None, "coordinates rounded to 0.1 degrees", None)]
EA_ROWS = [("7009", "Nearby candidatus", "Candidaceae", 24.0, 121.0, 6, 3),   # outside the JP mesh envelope: 1° only
           ("6001", "Proximus orientalis", "Fictaceae", 25.0, 127.0, 2, 2)]

# every private string that must not survive into the serialized files
PROSE = ["Hidden Corner Meadow", "decaying Pinus", "Castanopsis sieboldii", "1234",
         "location obscured", "coordinates rounded to 0.1", "Oki Isle", "Lil Isle"]

# eligible okisle/lilisle cells -> area; nothing flagged or name-only appears here
EXPECTED_CELLS = {"313:1021": "okisle",   # Prosus x2 + the genus row (corner + interior of one whole cell)
                  "317:1022": "okisle",   # Sanus communis
                  "322:1025": "t4028"}    # Neighborus proximus: no island claims the centre


def build(db, islands, refs, *extra):
    out = tempfile.mkdtemp(prefix="areas-")
    r = run_builder("build_areas.py", "--db", db, "--out", out, "--islands", islands, *refs, *extra)
    assert r.returncode == 0, r.stdout + r.stderr
    return out


def read(path):
    with open(path, "rb") as f:
        return f.read()


class SanitizedAreaDetail(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp()
        cls.islands = os.path.join(cls.tmp, "islands.csv")
        with open(cls.islands, "w") as f:
            f.write(ISLANDS_CSV)
        cls.refs = ["--mask", os.path.join(FIX, "taxon_mask.csv"),
                    "--coverage", write_coverage(cls.tmp),
                    "--exceptions", os.path.join(FIX, "match_exceptions.csv"),
                    "--redlist", os.path.join(FIX, "redlist.json")]
        cls.db = os.path.join(cls.tmp, "flags.duckdb")
        c = duckdb.connect(cls.db)
        c.execute("create table jp(gbifID varchar, " + JP_COLUMNS + FLAG_COLUMNS + ")")
        marks = ",".join(["?"] * 19)
        c.executemany(f"insert into jp values ({marks})", JP_ROWS)
        c.execute("create table jp_extra(gbifID varchar, habitat varchar, assoc varchar, elevation double, "
                  "dataGeneralizations varchar, informationWithheld varchar)")
        c.executemany("insert into jp_extra values (?,?,?,?,?,?)", XE_ROWS)
        c.execute("create table ea(speciesKey varchar, species varchar, family varchar, "
                  "lat double, lon double, month int, n int)")
        c.executemany("insert into ea values (?,?,?,?,?,?,?)", EA_ROWS)
        c.close()
        cls.areas = build(cls.db, cls.islands, cls.refs)
        cls.raw = {n: read(os.path.join(cls.areas, n)) for n in sorted(os.listdir(cls.areas))}
        cls.ok = json.loads(cls.raw["okisle.json"])
        cls.lil = json.loads(cls.raw["lilisle.json"])
        cls.index = json.loads(cls.raw["index.json"])
        cls.group = json.loads(cls.raw["g-okinawa-prefecture.json"])

    @classmethod
    def oki_center(cls):
        """The public area centre, by independent arithmetic: mean of the JIS cell centres
        whose centres lie inside the okisle box 26.0-26.6 x 127.5-128.0."""
        rows = [r for r in range(312, 319) if 26.0 <= (r + 0.5) / 12 <= 26.6]
        cols = [c for c in range(1020, 1024) if 127.5 <= (c + 0.5) / 8 <= 128.0]
        return (sum(round((r + 0.5) / 12, 4) for r in rows) / len(rows),
                sum(round((c + 0.5) / 8, 4) for c in cols) / len(cols))

    def candidates(self, area, name):
        return next((p for p in area["possible"] if p["n"] == name), None)

    def test_no_occurrence_derived_prose_in_any_file(self):
        for name, blob in self.raw.items():
            text = blob.decode()
            for secret in PROSE:
                self.assertNotIn(secret, text, f"{secret!r} leaked into {name}")

    def test_withdrawn_fields_are_empty_and_columns_kept(self):
        row = next(s for s in self.ok["species"] if s["n"] == "Prosus lautus")
        self.assertEqual((row["h"], row["l"], row["nm"], row["el"]), ([], [], 0, None))
        self.assertEqual(sorted(row), ["e", "el", "f", "h", "k", "l", "m", "n", "nm", "r", "sq", "y"])
        self.assertEqual((row["r"], row["sq"], row["y"]), (2, 1, [2019, 2021]))  # corner + interior: one whole cell
        self.assertEqual(self.ok["top_localities"], [])
        self.assertEqual(self.ok["totals"]["by_name"], 0)
        self.assertEqual(self.index["has_habitat"], False)
        for blob in self.raw.values():
            self.assertNotIn(b'"h":[{', blob)   # no habitat string ever starts a list
            self.assertNotIn(b'"el":[', blob)   # elevation is never a [min, max] pair

    def test_totals_keep_ineligible_records_but_squares_months_do_not(self):
        # 10 records: 4 spatial + 5 flag-routed + 1 name-only (flagged with no locality is dropped)
        self.assertEqual(self.ok["totals"], {"records": 10, "by_name": 0, "species": 8,
                                             "unresolved": 1, "squares": 2})
        self.assertEqual(self.ok["months"], [0, 0, 1, 0, 1, 0, 1, 0, 0, 0, 0, 1])  # months 3,5,7,12 only
        for name in ("Uncerus vagus", "Coarsus latus", "Flagellus corruptus",
                     "Obscurus celatus", "Generalizus latus", "Nominis tantum"):
            row = next(s for s in self.ok["species"] if s["n"] == name)
            self.assertEqual((row["r"], row["sq"], row["m"]), (1, 0, [0] * 12), name)

    def test_index_cells_come_from_eligible_whole_cells_only(self):
        self.assertEqual(self.index["cells"], EXPECTED_CELLS)
        for flagged in ("315:1021", "318:1022", "318:1023", "314:1020", "314:1021", "319:1024"):
            self.assertNotIn(flagged, self.index["cells"])   # cells held only by ineligible records

    def test_dropped_record_is_nowhere_and_no_ineligible_candidate_sources(self):
        names = {s["n"] for blob in self.raw.values() for s in json.loads(blob).get("species", [])}
        cands = {p["n"] for blob in self.raw.values() for p in json.loads(blob).get("possible", [])}
        self.assertNotIn("Ghostus absentis", names | cands)        # flagged, no name match: dropped
        self.assertNotIn("Phantomus lilii", cands)                 # flagged records seed no candidates
        self.assertIn("Phantomus lilii", {s["n"] for s in self.lil["species"]})   # but count in totals
        self.assertEqual(self.lil["totals"], {"records": 1, "by_name": 0, "species": 1,
                                              "unresolved": 0, "squares": 0})

    def test_candidate_distance_bands_from_public_geography(self):
        lat, lon = self.oki_center()
        jp = self.candidates(self.ok, "Neighborus proximus")
        src = cell_center(*mesh_cell(26.9, 128.15))               # snapped source-cell centroid
        self.assertEqual(jp["d"], BAND(haversine_km(lat, lon, src[0], src[1])))
        self.assertEqual(jp["s"], 100)                             # top of its kind
        self.assertEqual(jp["m"][5], 1)                            # eligible occurrences only
        for name, point in (("Nearby candidatus", (24.0, 121.0)),   # East Asia: native 1°, outside the JP mesh
                            ("Proximus orientalis", (25.0, 127.0))):
            row = self.candidates(self.ok, name)
            self.assertEqual(row["d"], BAND(haversine_km(lat, lon, *point)), name)
            self.assertTrue(1 <= row["s"] <= 100, name)
        self.assertEqual((self.candidates(self.ok, "Nearby candidatus")["w"],
                          self.candidates(self.ok, "Nearby candidatus")["wj"]), ("Taiwan", "台湾"))
        self.assertEqual(self.candidates(self.ok, "Nearby candidatus")["m"][5], 3)
        for blob in self.raw.values():
            for p in json.loads(blob).get("possible", []):
                self.assertIn(p["d"], (0, 50, 100, 150, 200, 250))

    def test_east_asia_point_outside_mesh_envelope_survives(self):
        # (24,121) cannot be snapped to a 2nd mesh cell: its presence proves East Asia
        # candidates score from the raw 1° cell, not from JIS-snapped centroids
        self.assertIsNotNone(self.candidates(self.ok, "Nearby candidatus"))

    def test_within_cell_move_changes_no_candidate_bytes(self):
        db = os.path.join(self.tmp, "moved.duckdb")
        c = duckdb.connect(db)
        c.execute("create table jp(gbifID varchar, " + JP_COLUMNS + FLAG_COLUMNS + ")")
        moved = [list(r) for r in JP_ROWS]
        moved[9][9], moved[9][10] = "26.88", "128.2"   # Neighborus stays in cell 322:1025
        c.executemany("insert into jp values (" + ",".join(["?"] * 19) + ")", [tuple(r) for r in moved])
        c.execute("create table jp_extra(gbifID varchar, habitat varchar, assoc varchar, elevation double, "
                  "dataGeneralizations varchar, informationWithheld varchar)")
        c.executemany("insert into jp_extra values (?,?,?,?,?,?)", XE_ROWS)
        c.execute("create table ea(speciesKey varchar, species varchar, family varchar, "
                  "lat double, lon double, month int, n int)")
        c.executemany("insert into ea values (?,?,?,?,?,?,?)", EA_ROWS)
        c.close()
        for name in ("okisle.json", "t4028.json", "index.json"):
            self.assertEqual(read(os.path.join(build(db, self.islands, self.refs), name)), self.raw[name], name)

    def test_groups_derive_from_complete_cell_unions(self):
        self.assertEqual(self.group["members"], ["okisle", "lilisle"])
        self.assertEqual(self.group["bbox"], [26.0, 26.8, 127.5, 128.2])
        self.assertEqual(self.group["totals"], {"records": 11, "by_name": 0, "species": 9,
                                                "unresolved": 1, "squares": 2})
        self.assertEqual(self.group["months"], self.ok["months"])
        self.assertEqual(self.group["top_localities"], [])
        row = next(s for s in self.group["species"] if s["n"] == "Prosus lautus")
        self.assertEqual((row["h"], row["l"], row["nm"], row["el"], row["sq"], row["i"]), ([], [], 0, None, 1, ["okisle"]))
        # candidates pass through with the source member id
        self.assertEqual(self.candidates(self.group, "Neighborus proximus")["i"], "okisle")

    def test_missing_flag_columns_are_skipped_not_guessed(self):
        db = os.path.join(self.tmp, "noflags.duckdb")
        c = duckdb.connect(db)
        c.execute(f"create table jp({JP_COLUMNS})")     # no precision columns, no jp_extra
        c.executemany("insert into jp values (" + ",".join(["?"] * 15) + ")",
                      [tuple(r[i] for i in (1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15)) for r in JP_ROWS])
        c.execute("create table ea(speciesKey varchar, species varchar, family varchar, "
                  "lat double, lon double, month int, n int)")
        c.executemany("insert into ea values (?,?,?,?,?,?,?)", EA_ROWS)
        c.close()
        cells = json.loads(read(os.path.join(build(db, self.islands, self.refs), "index.json")))["cells"]
        self.assertIn("315:1021", cells)                # Uncerus (uncertainty 25000) is spatial when the column is absent
        self.assertEqual(cells["315:1021"], "okisle")


DWCA_FULL = ("gbifID\thabitat\tassociatedTaxa\televation\tdataGeneralizations\tinformationWithheld\n"
             "1\ton dead Quercus wood\tQuercus sp.\t500\t\t\n"
             "2\t\t\t\t\tlocality withheld\n")
DWCA_THIN = ("gbifID\thabitat\tassociatedTaxa\televation\n"   # an archive without the precision fields
             "1\tlitter\t\t\n")
DWCA_ULTRATHIN = "gbifID\trecordedBy\n1\tsomeone\n"           # only the join key (TASK-006 header guard)


class BuildDbEligibilityFields(unittest.TestCase):
    """build_db.py gains the DwC-A eligibility pass; an archive without the fields
    still builds, with null columns instead of a guess."""

    def load(self, dwca):
        tmp = tempfile.mkdtemp()
        paths = {}
        for name, text in (("japan", "speciesKey\tspecies\n5001\tFungus albus\n"),
                           ("neighbours", "species\tfamily\nFungus albus\tFictaceae\n"),
                           ("mo", "speciesKey\tcountryCode\n5001\tJP\n"), ("dwca", dwca)):
            paths[name] = os.path.join(tmp, name + ".tsv")
            with open(paths[name], "w") as f:
                f.write(text)
        db = os.path.join(tmp, "db.duckdb")
        r = run_builder("build_db.py", "--japan", paths["japan"], "--neighbours", paths["neighbours"],
                        "--mo", paths["mo"], "--japan-dwca", paths["dwca"], "--db", db)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        return duckdb.connect(db, read_only=True)

    def test_full_archive_loads_precision_fields(self):
        c = self.load(DWCA_FULL)
        rows = c.execute("select gbifID, habitat, assoc, elevation, dataGeneralizations, "
                         "informationWithheld from jp_extra order by gbifID").fetchall()
        c.close()
        self.assertEqual(rows, [("1", "on dead Quercus wood", "Quercus sp.", 500.0, None, None),
                                ("2", None, None, None, None, "locality withheld")])

    def test_thin_archive_gets_null_columns(self):
        c = self.load(DWCA_THIN)
        cols = {r[0] for r in c.execute("select column_name from information_schema.columns "
                                        "where table_name='jp_extra'").fetchall()}
        n = c.execute("select count(*), count(dataGeneralizations), count(informationWithheld) "
                      "from jp_extra").fetchone()
        c.close()
        self.assertIn("dataGeneralizations", cols)
        self.assertIn("informationWithheld", cols)
        self.assertEqual(n, (1, 0, 0))

    def test_archive_without_detail_columns_still_builds(self):
        # habitat/associatedTaxa/elevation are header-guarded like the precision fields:
        # an archive without them builds, with null columns instead of a SQL error
        c = self.load(DWCA_ULTRATHIN)
        rows = c.execute("select habitat, assoc, elevation, dataGeneralizations, informationWithheld "
                         "from jp_extra").fetchall()
        c.close()
        self.assertEqual(rows, [(None, None, None, None, None)])


class FruitingEvidenceMonths(unittest.TestCase):
    """TASK-012: area month histograms count fruiting evidence only (basisOfRecord
    MATERIAL_SAMPLE excluded) — months_all, per-species m, the candidate m and the
    group month sums — while record totals, squares, years and evidence breakdowns
    keep every record."""

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp()
        cls.islands = os.path.join(cls.tmp, "islands.csv")
        with open(cls.islands, "w") as f:
            f.write(ISLANDS_CSV)
        cls.refs = ["--mask", os.path.join(FIX, "taxon_mask.csv"),
                    "--coverage", write_coverage(cls.tmp),
                    "--exceptions", os.path.join(FIX, "match_exceptions.csv"),
                    "--redlist", os.path.join(FIX, "redlist.json")]
        cls.db = os.path.join(cls.tmp, "evid.duckdb")
        c = duckdb.connect(cls.db)
        c.execute("create table jp(gbifID varchar, " + JP_COLUMNS + FLAG_COLUMNS + ")")
        marks = ",".join(["?"] * 19)
        c.executemany(f"insert into jp values ({marks})", [
            # okisle cell 313:1021: Prosus with observation (m3), observation (m3),
            # DNA sample (m2, excluded) and specimen (m2, counted — fruiting evidence)
            ("e1", "5101", "Prosus lautus", "Prosus", "Fictaceae", "SPECIES", "HUMAN_OBSERVATION",
             "Agaricomycetes", "Basidiomycota", "26.1", "127.7", "3", "2020", None, "Okinawa",
             "dx", None, None, None),
            ("e2", "5101", "Prosus lautus", "Prosus", "Fictaceae", "SPECIES", "HUMAN_OBSERVATION",
             "Agaricomycetes", "Basidiomycota", "26.1", "127.7", "3", "2020", None, "Okinawa",
             "dx", None, None, None),
            ("e3", "5101", "Prosus lautus", "Prosus", "Fictaceae", "SPECIES", "MATERIAL_SAMPLE",
             "Agaricomycetes", "Basidiomycota", "26.1", "127.7", "2", "2020", None, "Okinawa",
             "dx", None, None, None),
            ("e4", "5101", "Prosus lautus", "Prosus", "Fictaceae", "SPECIES", "PRESERVED_SPECIMEN",
             "Agaricomycetes", "Basidiomycota", "26.1", "127.7", "2", "2020", None, "Okinawa",
             "dx", None, None, None),
            # tile t4028 cell 322:1025: Candidus with one observation (m5) and two DNA
            # samples (m9) — the candidate histogram for okisle must keep m5 only
            ("e5", "5102", "Candidus alter", "Candidus", "Fictaceae", "SPECIES", "HUMAN_OBSERVATION",
             "Agaricomycetes", "Basidiomycota", "26.9", "128.15", "5", "2020", None, "Okinawa",
             "dx", None, None, None),
            ("e6", "5102", "Candidus alter", "Candidus", "Fictaceae", "SPECIES", "MATERIAL_SAMPLE",
             "Agaricomycetes", "Basidiomycota", "26.9", "128.15", "9", "2020", None, "Okinawa",
             "dx", None, None, None),
            ("e7", "5102", "Candidus alter", "Candidus", "Fictaceae", "SPECIES", "MATERIAL_SAMPLE",
             "Agaricomycetes", "Basidiomycota", "26.9", "128.15", "9", "2020", None, "Okinawa",
             "dx", None, None, None),
        ])
        c.close()
        cls.areas = build(cls.db, cls.islands, cls.refs)
        with open(os.path.join(cls.areas, "okisle.json")) as f:
            cls.ok = json.load(f)
        with open(os.path.join(cls.areas, "t4028.json")) as f:
            cls.tile = json.load(f)
        with open(os.path.join(cls.areas, "g-okinawa-prefecture.json")) as f:
            cls.group = json.load(f)

    def test_area_months_and_species_m_exclude_dna_months(self):
        # months 2 (specimen) and 3 (observations) count; the month-2 DNA sample does not
        self.assertEqual(self.ok["months"], [0, 1, 2, 0, 0, 0, 0, 0, 0, 0, 0, 0])
        row = next(s for s in self.ok["species"] if s["n"] == "Prosus lautus")
        self.assertEqual(row["m"], [0, 1, 2, 0, 0, 0, 0, 0, 0, 0, 0, 0])
        # totals keep every record: 4 incl. the DNA sample, square kept, evidence kept
        self.assertEqual((row["r"], row["sq"], row["e"]), (4, 1, {"o": 2, "d": 1, "s": 1}))
        self.assertEqual(self.ok["totals"]["records"], 4)

    def test_candidate_months_exclude_dna_months(self):
        cand = next(p for p in self.ok["possible"] if p["n"] == "Candidus alter")
        self.assertEqual(cand["m"], [0, 0, 0, 0, 1, 0, 0, 0, 0, 0, 0, 0])   # m5 only, m9 excluded
        tile_row = next(s for s in self.tile["species"] if s["n"] == "Candidus alter")
        self.assertEqual((tile_row["r"], tile_row["m"][4], tile_row["m"][8]), (3, 1, 0))
        self.assertEqual(self.tile["months"], [0, 0, 0, 0, 1, 0, 0, 0, 0, 0, 0, 0])
        self.assertEqual(self.tile["totals"]["records"], 3)                  # DNA still counted here

    def test_group_months_sum_the_excluded_member_months(self):
        self.assertEqual(self.group["months"], self.ok["months"])           # transitively 'd'-free
        row = next(s for s in self.group["species"] if s["n"] == "Prosus lautus")
        self.assertEqual(row["m"], [0, 1, 2, 0, 0, 0, 0, 0, 0, 0, 0, 0])


class DominantSource(unittest.TestCase):
    """TASK-012: per area, dominant_source [title, share] for the top datasetKey
    when it holds >= 30% of the area's records — titles resolved offline via
    --titles, the bare datasetKey when resolution fails."""

    def area_with(self, datasets, titles=None):
        tmp = tempfile.mkdtemp()
        db = os.path.join(tmp, "dom.duckdb")
        c = duckdb.connect(db)
        c.execute("create table jp(gbifID varchar, " + JP_COLUMNS + FLAG_COLUMNS + ")")
        c.executemany("insert into jp values (" + ",".join(["?"] * 19) + ")",
                      [(f"g{i}", "5101", "Prosus lautus", "Prosus", "Fictaceae", "SPECIES",
                        "HUMAN_OBSERVATION", "Agaricomycetes", "Basidiomycota", "26.1", "127.7",
                        "3", "2020", None, "Okinawa", d, None, None, None)
                       for i, d in enumerate(datasets)])
        c.close()
        islands = os.path.join(tmp, "islands.csv")
        with open(islands, "w") as f:
            f.write(ISLANDS_CSV)
        extra = ()
        if titles is not None:
            tp = os.path.join(tmp, "titles.json")
            with open(tp, "w") as f:
                json.dump(titles, f)
            extra = ("--titles", tp)
        out = build(db, islands, ["--mask", os.path.join(FIX, "taxon_mask.csv"),
                                  "--coverage", write_coverage(tmp),
                                  "--exceptions", os.path.join(FIX, "match_exceptions.csv"),
                                  "--redlist", os.path.join(FIX, "redlist.json")], *extra)
        with open(os.path.join(out, "okisle.json")) as f:
            return json.load(f)

    def test_two_thirds_ships_title_and_share(self):
        area = self.area_with(["a", "a", "a", "a", "b", "b"],
                              {"a": "Survey A", "b": "Survey B"})
        self.assertEqual(area["dominant_source"], ["Survey A", 0.67])

    def test_exactly_thirty_percent_ships(self):
        area = self.area_with(["c"] * 3 + ["d"] * 3 + ["e"] * 2 + ["f"] * 2,
                              {"c": "Survey C", "d": "D", "e": "E", "f": "F"})
        self.assertEqual(area["dominant_source"], ["Survey C", 0.3])

    def test_below_thirty_percent_omits_the_key(self):
        area = self.area_with(["a", "b", "c", "d"], {"a": "A", "b": "B", "c": "C", "d": "D"})
        self.assertNotIn("dominant_source", area)

    def test_title_fetch_failure_falls_back_to_the_bare_key(self):
        area = self.area_with(["not-a-real-key"] * 4)      # absent from any map: fetch fails
        self.assertEqual(area["dominant_source"], ["not-a-real-key", 1.0])


if __name__ == "__main__":
    unittest.main()
