"""TASK-002: prove the shared gate on every public output, end to end.

Builds a scratch DuckDB of fictional taxa — including protected ones that the
old per-builder predicates would have leaked (null-key alias, fuzzy Red List
match, masked-genus genus-only row) — then runs both real builders against it
and asserts what appears in the public JSON. The raw table still contains the
masked taxa; the outputs must not.
"""
import json, os, subprocess, sys, tempfile, unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import duckdb

from test_public_taxa import FIX, write_coverage

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

JP_COLUMNS = "speciesKey varchar, species varchar, genus varchar, family varchar, taxonRank varchar, \
basisOfRecord varchar, class varchar, phylum varchar, decimalLatitude varchar, decimalLongitude varchar, \
month varchar, year varchar, locality varchar, stateProvince varchar, datasetKey varchar"

# (key, species, genus, family, rank, basis, class, phylum, lat, lon, month, year, locality, pref, dataset)
JP_ROWS = [
    ("7001", "Matsutakus fictus", "Matsutakus", "Fictaceae", "SPECIES", "HUMAN_OBSERVATION",
     "Agaricomycetes", "Basidiomycota", "26.50", "127.90", "7", "2020", "Secret grove", "Okinawa", "dx"),
    (None, "Tricholoma fictum", "Tricholoma", "Fictaceae", "SPECIES", "PRESERVED_SPECIMEN",
     "Agaricomycetes", "Basidiomycota", "26.50", "127.90", "8", "2019", None, "Okinawa", "dx"),
    ("7002", "Publicus bonum", "Publicus", "Fictaceae", "SPECIES", "HUMAN_OBSERVATION",
     "Agaricomycetes", "Basidiomycota", "26.50", "127.90", "7", "2021", None, "Okinawa",
     "d714382d-5890-4234-ae81-696eeb53658a"),
    (None, None, "Matsutakus", "Fictaceae", "GENUS", "HUMAN_OBSERVATION",
     "Agaricomycetes", "Basidiomycota", "26.60", "128.00", None, "2018", None, "Okinawa", "dx"),
    ("9001", "Rareus japonicus", "Rareus", "Rareaceae", "SPECIES", "PRESERVED_SPECIMEN",
     "Agaricomycetes", "Basidiomycota", "26.70", "128.10", "6", "2017", None, "Okinawa", "dx"),
    ("9002", "Dubius ambiguus", "Dubius", "Dubiaceae", "SPECIES", "HUMAN_OBSERVATION",
     "Agaricomycetes", "Basidiomycota", "26.80", "128.20", "5", "2016", None, "Okinawa", "dx"),
    (None, "Rareus japonicus var. albus", "Rareus", "Rareaceae", "VARIETY", "PRESERVED_SPECIMEN",
     "Agaricomycetes", "Basidiomycota", "26.50", "127.90", "9", "2015", None, "Okinawa", "dx"),
    (None, None, "Cleanus", "Fictaceae", "GENUS", "HUMAN_OBSERVATION",
     "Agaricomycetes", "Basidiomycota", "26.50", "127.90", None, "2010", None, "Okinawa", "dx"),
]
NB_ROWS = [  # (species, phylum, order, family, genus, class, nOcc, iucn, rank, status, key)
    ("Matsutakus fictus", "Basidiomycota", "Agaricales", "Fictaceae", "Matsutakus", "Agaricomycetes", "50", None, "SPECIES", "ACCEPTED", "7001"),
    ("Dubius ambiguus", "Basidiomycota", "Agaricales", "Dubiaceae", "Dubius", "Agaricomycetes", "40", None, "SPECIES", "ACCEPTED", "9002"),
    ("Nearby candidatus", "Basidiomycota", "Agaricales", "Candidaceae", "Nearby", "Agaricomycetes", "30", None, "SPECIES", "ACCEPTED", "7009"),
]
MO_ROWS = [("7001", "JP"), ("9002", "JP"), ("7009", "JP")]
EA_ROWS = [("7009", "Nearby candidatus", "Candidaceae", 26.0, 127.0, 6, 3),
           ("9002", "Dubius ambiguus", "Dubiaceae", 25.0, 126.0, 5, 2),
           (None, "Boletus fictus", "Fictaceae", 25.5, 126.5, 4, 1)]

MASKED_JP = 6          # rows 1, 2, 4, 5, 6, 7 above
PUBLIC_MESH_RECORDS = 2  # Publicus bonum + the Cleanus genus-only row, both in cell 26.50/127.90


def run_builder(script, *args):
    return subprocess.run([sys.executable, os.path.join(REPO, "pipeline", script), *args],
                          capture_output=True, text=True, cwd=REPO)


class GatedOutputs(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp()
        cls.db = os.path.join(cls.tmp, "scratch.duckdb")
        c = duckdb.connect(cls.db)
        c.execute(f"create table jp({JP_COLUMNS})")
        c.executemany("insert into jp values (" + ",".join(["?"] * 15) + ")", JP_ROWS)
        c.execute('create table nb(species varchar, phylum varchar, "order" varchar, family varchar, genus varchar, '
                  "class varchar, numberOfOccurrences varchar, iucnRedListCategory varchar, taxonRank varchar, "
                  "taxonomicStatus varchar, speciesKey varchar)")
        c.executemany("insert into nb values (" + ",".join(["?"] * 11) + ")", NB_ROWS)
        c.execute("create table mo(speciesKey varchar, countryCode varchar)")
        c.executemany("insert into mo values (?, ?)", MO_ROWS)
        c.execute("create table ea(speciesKey varchar, species varchar, family varchar, lat double, "
                  "lon double, month int, n int)")
        c.executemany("insert into ea values (?, ?, ?, ?, ?, ?, ?)", EA_ROWS)
        c.close()
        cls.cov = write_coverage(cls.tmp)
        cls.refs = ["--mask", os.path.join(FIX, "taxon_mask.csv"), "--coverage", cls.cov,
                    "--exceptions", os.path.join(FIX, "match_exceptions.csv"),
                    "--redlist", os.path.join(FIX, "redlist.json")]
        cls.dash = os.path.join(cls.tmp, "dashboard.json")
        r = run_builder("build_dashboard_data.py", "--db", cls.db, "--out", cls.dash, *cls.refs)
        assert r.returncode == 0, r.stderr
        with open(cls.dash) as f:
            cls.out = json.load(f)
        cls.areas = os.path.join(cls.tmp, "areas")
        r = run_builder("build_areas.py", "--db", cls.db, "--out", cls.areas,
                        "--islands", os.path.join(FIX, "islands.csv"), *cls.refs)
        assert r.returncode == 0, r.stderr
        with open(os.path.join(cls.areas, "testisle.json")) as f:
            cls.area = json.load(f)
        with open(os.path.join(cls.areas, "index.json")) as f:
            cls.index = json.load(f)

    def test_masked_taxa_are_in_the_raw_db(self):
        c = duckdb.connect(self.db, read_only=True)
        self.assertEqual(c.execute("select count(*) from jp where species='Tricholoma fictum'").fetchone()[0], 1)
        self.assertEqual(c.execute("select count(*) from jp where genus='Matsutakus' and taxonRank='GENUS'").fetchone()[0], 1)
        c.close()

    def test_mesh_excludes_masked_records_and_genus_only_cells(self):
        mesh = self.out["mesh"]
        self.assertEqual(len(mesh), 1)                       # cells holding only masked rows are gone
        self.assertEqual(mesh[0][2], PUBLIC_MESH_RECORDS)   # records
        self.assertEqual(mesh[0][3], 1)                      # distinct species (Publicus bonum)

    def test_families_exclude_families_of_only_masked_species(self):
        fams = [f[0] for f in self.out["families"]]
        self.assertIn("Fictaceae", fams)
        self.assertNotIn("Rareaceae", fams)
        self.assertNotIn("Dubiaceae", fams)
        fict = next(f for f in self.out["families"] if f[0] == "Fictaceae")
        self.assertEqual(fict[1:], [1, PUBLIC_MESH_RECORDS])  # species, records

    def test_national_leads_exclude_masked_and_ambiguous(self):
        self.assertEqual({l[0] for l in self.out["leads"]}, {"Nearby candidatus"})
        self.assertEqual(self.out["lead_totals"]["with_mo"], 1)

    def test_unresolved_genera_exclude_masked_genera(self):
        self.assertEqual([g[0] for g in self.out["unresolved_genera"]], ["Cleanus"])

    def test_dashboard_totals_stay_nationwide_aggregates(self):
        self.assertEqual(self.out["totals"]["sensitive_records"], MASKED_JP)
        self.assertEqual(self.out["totals"]["sensitive_species"], 3)  # keys 7001, 9001, 9002
        self.assertEqual(self.out["totals"]["redlist_names"], 3)

    def test_area_species_and_months_exclude_masked(self):
        self.assertEqual([s["n"] for s in self.area["species"]], ["Publicus bonum"])
        self.assertEqual(sum(self.area["months"]), 1)
        self.assertEqual(self.area["months"][6], 1)
        self.assertEqual(self.area["totals"]["records"], PUBLIC_MESH_RECORDS)

    def test_area_candidates_exclude_masked_east_asia_and_aliases(self):
        poss = {p["n"] for p in self.area["possible"]}
        self.assertIn("Nearby candidatus", poss)
        for leaked in ("Dubius ambiguus", "Boletus fictus", "Matsutakus fictus", "Tricholoma fictum"):
            self.assertNotIn(leaked, poss)

    def test_index_reports_masked_aggregate_and_public_cells_only(self):
        self.assertEqual(self.index["masked_records"], MASKED_JP)
        self.assertEqual(self.index["cells"], {"318:1023": "testisle"})


class BuildersFailClosed(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp()
        cls.db = os.path.join(cls.tmp, "scratch.duckdb")
        c = duckdb.connect(cls.db)
        c.execute(f"create table jp({JP_COLUMNS})")
        c.execute("create table nb(species varchar, phylum varchar, \"order\" varchar, family varchar, genus varchar, "
                  "class varchar, numberOfOccurrences varchar, iucnRedListCategory varchar, taxonRank varchar, "
                  "taxonomicStatus varchar, speciesKey varchar)")
        c.execute("create table mo(speciesKey varchar, countryCode varchar)")
        c.close()

    def check(self, script, out_name, *extra):
        out = os.path.join(self.tmp, out_name)
        r = run_builder(script, "--db", self.db, "--out", out, *extra)
        self.assertNotEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertFalse(os.path.exists(out), "blocked build must not write output")
        self.assertIn("PublicTaxaError", r.stderr)
        return r.stderr

    def test_missing_redlist_blocks_dashboard(self):
        err = self.check("build_dashboard_data.py", "d1.json")
        self.assertIn("Red List download missing", err)

    def test_unreviewed_repo_seed_coverage_blocks_dashboard(self):
        # fixture inputs but the repo's empty coverage seed: no publish until reviewed
        err = self.check("build_dashboard_data.py", "d2.json",
                         "--mask", os.path.join(FIX, "taxon_mask.csv"),
                         "--exceptions", os.path.join(FIX, "match_exceptions.csv"),
                         "--redlist", os.path.join(FIX, "redlist.json"))
        self.assertIn("coverage manifest is empty", err)

    def test_missing_mask_blocks_areas(self):
        err = self.check("build_areas.py", os.path.join("a1", "x"),
                         "--redlist", os.path.join(FIX, "redlist.json"),
                         "--coverage", write_coverage(self.tmp),
                         "--mask", os.path.join(self.tmp, "nope.csv"))
        self.assertIn("commercial taxon mask missing", err)


if __name__ == "__main__":
    unittest.main()
