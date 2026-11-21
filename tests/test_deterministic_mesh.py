"""TASK-004: one canonical JIS mesh geometry in pipeline/geo_ja.py and the
determinism it buys — boundary semantics, the JS parity fixture, and builder
invariance: row-shuffle byte identity, within-cell moves, island-box edge
ownership, dashboard/index key equality and zero-record island routes.

Fictional taxa only, as in test_public_taxa_outputs.py.
"""
import json, os, random, sys, tempfile, unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import duckdb

from pipeline.geo_ja import (boundary_fixture, cell_center, cell_key, cell_rect,  # noqa: E402
                             mesh_cell, mesh_key, tile_id)
from test_public_taxa import FIX, write_coverage  # noqa: E402
from test_public_taxa_outputs import JP_COLUMNS, run_builder  # noqa: E402

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PARITY = os.path.join(REPO, "tests", "fixtures", "mesh_boundary.json")


class MeshGeometry(unittest.TestCase):
    def test_envelope_is_half_open_on_cells(self):
        self.assertEqual(mesh_cell(20.0, 122.0), (240, 976))                 # exact south/west edges: first cell
        self.assertEqual(mesh_cell(45.9999999, 153.9999999), (551, 1231))   # top row, easternmost column
        self.assertIsNone(mesh_cell(46.0, 122.0))                           # north edge is half-open
        self.assertIsNone(mesh_cell(20.0, 154.0))                           # east edge is half-open
        self.assertIsNone(mesh_cell(19.9999999, 122.0))                     # just south
        self.assertIsNone(mesh_cell(20.0, 121.9999999))                     # just west

    def test_epsilon_snaps_float_noise_below_an_edge(self):
        self.assertEqual(mesh_cell(313 / 12, 127.875), (313, 1023))  # the double just below 313/12
        self.assertEqual(mesh_cell(26.5, 127.875), (318, 1023))      # exact float edges: own cell

    def test_within_cell_moves_keep_the_cell(self):
        self.assertEqual(mesh_cell(26.5, 127.875), mesh_cell(26.51, 127.9))

    def test_non_finite_and_missing_are_rejected(self):
        for lat, lon in ((float("nan"), 130.0), (26.5, float("inf")), (None, 130.0), (26.5, None)):
            self.assertIsNone(mesh_cell(lat, lon), (lat, lon))
            self.assertIsNone(mesh_key(lat, lon), (lat, lon))

    def test_geometry_reconstructible_from_integer_keys_alone(self):
        self.assertEqual(mesh_key(26.5, 127.875), "318:1023")
        self.assertEqual(cell_key(318, 1023), "318:1023")
        self.assertEqual(cell_rect(318, 1023), [26.5, 319 / 12, 127.875, 128.0])
        self.assertEqual(cell_center(318, 1023), [26.5417, 127.9375])
        self.assertEqual(mesh_cell(*cell_center(318, 1023)), (318, 1023))  # centre lands in its own cell

    def test_tile_id_partitions_cells_into_first_meshes(self):
        self.assertEqual(tile_id(312, 1020), "t3927")
        self.assertEqual(tile_id(322, 1026), "t4028")
        for p1, u1 in ((30, 22), (40, 27), (55, 54)):   # every tile corner cell maps to its tile
            self.assertEqual(tile_id(8 * p1, 8 * (u1 + 100)), f"t{p1}{u1:02d}")


class ParityFixture(unittest.TestCase):
    def test_committed_fixture_is_what_the_module_generates(self):
        with open(PARITY) as f:
            self.assertEqual(json.load(f), boundary_fixture())

    def test_fixture_covers_rejected_and_accepted_points(self):
        pts = boundary_fixture()["points"]
        self.assertTrue(any(p["key"] is None for p in pts))
        self.assertTrue(any(p["key"] == "240:976" for p in pts))
        for p in pts:
            self.assertEqual(p["key"] is None, p["center"] is None)
            if p["key"] is not None:
                self.assertEqual(p["key"], f'{p["row"]}:{p["col"]}')
                self.assertEqual(mesh_cell(p["lat"], p["lon"]), (p["row"], p["col"]))


# bigisle/lillisle boxes overlap at [26.4,26.6] x [127.7,128.0]: cell centres there
# must resolve to bigisle (first row of the CSV = explicit priority). emptyisle
# gets no records at all and must still keep its route.
ISLANDS_CSV = """id,name_en,name_ja,group_en,group_ja,lat_min,lat_max,lon_min,lon_max,prefecture,aliases
bigisle,Big Island,大島,Big Islands,大諸島,26.0,26.6,127.5,128.0,Okinawa,big isle|大島
lillisle,Lil Island,小島,Big Islands,大諸島,26.4,26.8,127.7,128.2,Okinawa,lil isle|小島
emptyisle,Empty Island,空島,Big Islands,大諸島,25.0,25.2,126.0,126.2,Okinawa,empty isle|空島
"""

# (key, species, genus, family, rank, basis, class, phylum, lat, lon, month, year, locality, pref, dataset)
DET_ROWS = [
    ("8001", "Fungus albus", "Fungus", "Fictaceae", "SPECIES", "HUMAN_OBSERVATION", "Agaricomycetes",
     "Basidiomycota", "26.05", "127.55", "3", "2019", "Alpha", "Okinawa", "dx"),
    ("8001", "Fungus albus", "Fungus", "Fictaceae", "SPECIES", "PRESERVED_SPECIMEN", "Agaricomycetes",
     "Basidiomycota", "26.5", "127.75", "7", "2020", "Beta", "Okinawa", "dx"),
    ("8002", "Fungus ruber", "Fungus", "Fictaceae", "SPECIES", "PRESERVED_SPECIMEN", "Agaricomycetes",
     "Basidiomycota", "26.71", "128.11", "5", "2018", None, "Okinawa", "dx"),
    ("8002", "Fungus ruber", "Fungus", "Fictaceae", "SPECIES", "HUMAN_OBSERVATION", "Agaricomycetes",
     "Basidiomycota", "26.9", "128.3", "9", "2021", None, "Okinawa", "dx"),
    ("8003", "Fungus borealis", "Fungus", "Fictaceae", "SPECIES", "HUMAN_OBSERVATION", "Agaricomycetes",
     "Basidiomycota", "46.0", "154.0", "2", "2017", None, "Okinawa", "dx"),        # north/east edge: rejected
    ("8004", "Fungus australis", "Fungus", "Fictaceae", "SPECIES", "HUMAN_OBSERVATION", "Agaricomycetes",
     "Basidiomycota", "19.9999999", "122.0", "1", "2015", None, "Okinawa", "dx"),  # just south: rejected
    ("8005", "Fungus epsilon", "Fungus", "Fictaceae", "SPECIES", "HUMAN_OBSERVATION", "Agaricomycetes",
     "Basidiomycota", "25.99999999995", "127.55", "4", "2016", None, "Okinawa", "dx"),  # snaps into 312:1020
    ("8006", "Fungus nanus", "Fungus", "Fictaceae", "SPECIES", "HUMAN_OBSERVATION", "Agaricomycetes",
     "Basidiomycota", "NaN", "127.9", "6", "2014", None, "Okinawa", "dx"),         # non-finite: rejected
    ("8007", None, "Fungus", "Fictaceae", "GENUS", None, "Agaricomycetes",
     "Basidiomycota", "26.2", "127.6", "8", "2013", None, "Okinawa", "dx"),
]

EXPECTED_CELLS = {"312:1020": "bigisle",   # spans bigisle's south edge (see the epsilon row above)
                  "314:1020": "bigisle",
                  "318:1022": "bigisle",   # inside both island boxes: CSV priority, not record order
                  "320:1024": "lillisle",  # centre outside bigisle: the smaller island owns it
                  "322:1026": "t4028"}     # no island claims the centre: the 1st-mesh tile


class BuilderDeterminism(unittest.TestCase):
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
        cls.runs = {"base": cls.build(DET_ROWS, "base")}
        shuffled = list(DET_ROWS)
        random.Random(20261121).shuffle(shuffled)
        cls.runs["shuffled"] = cls.build(shuffled, "shuffled")
        cls.runs["reversed"] = cls.build(list(reversed(DET_ROWS)), "reversed")

    @classmethod
    def build(cls, rows, tag):
        db = os.path.join(cls.tmp, f"{tag}.duckdb")
        c = duckdb.connect(db)
        c.execute(f"create table jp({JP_COLUMNS})")
        c.executemany("insert into jp values (" + ",".join(["?"] * 15) + ")", rows)
        c.execute('create table nb(species varchar, phylum varchar, "order" varchar, family varchar, genus varchar, '
                  "class varchar, numberOfOccurrences varchar, iucnRedListCategory varchar, taxonRank varchar, "
                  "taxonomicStatus varchar, speciesKey varchar)")
        c.execute("create table mo(speciesKey varchar, countryCode varchar)")
        c.close()
        out = os.path.join(cls.tmp, tag)
        os.makedirs(out, exist_ok=True)
        dash = os.path.join(out, "dashboard.json")
        r = run_builder("build_dashboard_data.py", "--db", db, "--out", dash, *cls.refs)
        assert r.returncode == 0, r.stdout + r.stderr
        areas = os.path.join(out, "areas")
        r = run_builder("build_areas.py", "--db", db, "--out", areas, "--islands", cls.islands, *cls.refs)
        assert r.returncode == 0, r.stdout + r.stderr
        return dash, areas

    @classmethod
    def area_tree(cls, areas):
        return {name: cls.read(os.path.join(areas, name)) for name in sorted(os.listdir(areas))}

    @staticmethod
    def read(path):
        with open(path, "rb") as f:
            return f.read()

    @classmethod
    def index_of(cls, tag):
        return json.loads(cls.read(os.path.join(cls.runs[tag][1], "index.json")))

    def test_row_shuffle_and_reversal_are_byte_identical(self):
        base_dash, base_areas = self.runs["base"]
        for tag in ("shuffled", "reversed"):
            dash, areas = self.runs[tag]
            self.assertEqual(self.read(dash), self.read(base_dash), f"dashboard differs ({tag})")
            self.assertEqual(self.area_tree(areas), self.area_tree(base_areas), f"area files differ ({tag})")

    def test_moving_a_valid_point_within_its_cell_changes_nothing(self):
        moved = [list(r) for r in DET_ROWS]
        moved[0][8], moved[0][9] = "26.075", "127.5625"   # still cell 312:1020
        moved[6][8], moved[6][9] = "26.08", "127.51"      # epsilon row, still cell 312:1020
        dash, areas = self.build([tuple(r) for r in moved], "moved")
        base_dash, base_areas = self.runs["base"]
        self.assertEqual(self.read(dash), self.read(base_dash))
        self.assertEqual(self.area_tree(areas), self.area_tree(base_areas))

    def test_dashboard_mesh_keys_match_index_cells_exactly(self):
        dash = json.loads(self.read(self.runs["base"][0]))
        keys = {cell_key(*mesh_cell(r[0], r[1])) for r in dash["mesh"]}
        self.assertNotIn(None, keys)
        self.assertEqual(len(keys), len(dash["mesh"]))
        self.assertEqual(keys, set(self.index_of("base")["cells"]))

    def test_cell_ownership_is_by_center_with_island_priority(self):
        for tag in self.runs:
            self.assertEqual(self.index_of(tag)["cells"], EXPECTED_CELLS, tag)

    def test_rejected_points_never_reach_the_outputs(self):
        dash = json.loads(self.read(self.runs["base"][0]))
        by_key = {cell_key(*mesh_cell(r[0], r[1])): r for r in dash["mesh"]}
        self.assertEqual(by_key["312:1020"][2:], [2, 2])   # albus + the epsilon row; the genus row is 314:1020
        names = set()
        for name in os.listdir(self.runs["base"][1]):
            area = json.loads(self.read(os.path.join(self.runs["base"][1], name)))
            names |= {s["n"] for s in area.get("species", []) if s["n"]}
        for rejected in ("Fungus borealis", "Fungus australis", "Fungus nanus"):
            self.assertNotIn(rejected, names)

    def test_zero_record_island_keeps_route_and_reference_bbox(self):
        empty = json.loads(self.read(os.path.join(self.runs["base"][1], "emptyisle.json")))
        self.assertEqual(empty["kind"], "island")
        self.assertEqual(empty["bbox"], [25.0, 25.2, 126.0, 126.2])
        self.assertEqual(empty["totals"], {"records": 0, "by_name": 0, "species": 0,
                                           "unresolved": 0, "squares": 0})
        self.assertEqual((empty["species"], empty["possible"], empty["top_localities"]),
                         ([], [], []))
        self.assertEqual(empty["months"], [0] * 12)
        row = next(a for a in self.index_of("base")["areas"] if a["id"] == "emptyisle")
        self.assertEqual((row["kind"], row["records"], row["species"]), ("island", 0, 0))


if __name__ == "__main__":
    unittest.main()
