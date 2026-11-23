"""TASK-024: per-genus species bundles, the enrichment join and the species/
publication checks (spec item 7).

Fictional taxa as in test_public_taxa_outputs.py / test_area_sanitization.py.
The fixture DB is COL-keyed for one species (CX1) and legacy-keyed for the
rest, so both identity paths (key/legacy_key, enrichment joins, gate scans)
are exercised in one build. The staging bundle is built the way
publish-dashboard.yml builds it: dashboard -> areas -> enrich -> species
bundles -> snapshot -> validate.
"""
import csv, hashlib, json, os, shutil, subprocess, sys, tempfile, unittest
import urllib.parse

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import duckdb  # noqa: E402

from test_public_taxa import FIX, write_coverage  # noqa: E402
from test_public_taxa_outputs import run_builder  # noqa: E402
from test_area_sanitization import ISLANDS_CSV  # noqa: E402

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
WORKFLOW = os.path.join(REPO, ".github", "workflows", "publish-dashboard.yml")

JP_COLS = ("gbifID varchar, speciesKey varchar, species varchar, genus varchar, family varchar, "
           "\"order\" varchar, taxonRank varchar, basisOfRecord varchar, class varchar, "
           "phylum varchar, decimalLatitude varchar, decimalLongitude varchar, month varchar, "
           "year varchar, locality varchar, stateProvince varchar, datasetKey varchar, "
           "col_key varchar, col_name varchar, col_family varchar, col_order varchar, "
           "col_class varchar, col_genus varchar")

# (gbifID, key, species, genus, family, order, rank, basis, class, phylum, lat, lon,
#  month, year, locality, pref, dataset, col_key, col_name, col_family, col_order,
#  col_class, col_genus)
JP_ROWS = [
    ("1", "5001", "Prosus lautus", "Prosus", "Fictaceae", "Agaricales", "SPECIES",
     "HUMAN_OBSERVATION", "Agaricomycetes", "Basidiomycota", "26.083333333333332", "127.625",
     "3", "2019", "Hidden Corner Meadow", "Okinawa", "dx", None, None, None, None, None, None),
    ("2", "5001", "Prosus lautus", "Prosus", "Fictaceae", "Agaricales", "SPECIES",
     "PRESERVED_SPECIMEN", "Agaricomycetes", "Basidiomycota", "26.1", "127.7", "5", "2021",
     None, "Okinawa", "dx", None, None, None, None, None, None),
    # MATERIAL_SAMPLE: counts in records_by_tier ('d') but never in fruiting_months
    ("3", "5001", "Prosus lautus", "Prosus", "Fictaceae", "Agaricales", "SPECIES",
     "MATERIAL_SAMPLE", "Agaricomycetes", "Basidiomycota", "26.1", "127.7", "8", "2012",
     None, "Okinawa", "dx", None, None, None, None, None, None),
    ("4", "5003", "Prosus alter", "Prosus", "Fictaceae", "Agaricales", "SPECIES",
     "HUMAN_OBSERVATION", "Agaricomycetes", "Basidiomycota", "26.2", "127.75", "9", "2005",
     None, "Okinawa", "dx", None, None, None, None, None, None),
    # the COL-keyed species: key CX1, no legacy fallback
    ("5", "5002", "Publicus bonum", "Publicus", "Fictaceae", "Agaricales", "SPECIES",
     "HUMAN_OBSERVATION", "Agaricomycetes", "Basidiomycota", "26.45", "127.85", "7", "2015",
     None, "Okinawa", "dx", "CX1", "Publicus bonum", "Fictaceae", "Agaricales",
     "Agaricomycetes", "Publicus"),
    # recorded only in the t4028 tile, nearby okisle: a could-occur candidate there
    ("6", "5009", "Neighborus proximus", "Neighborus", "Fictaceae", "Agaricales", "SPECIES",
     "HUMAN_OBSERVATION", "Agaricomycetes", "Basidiomycota", "26.9", "128.15", "6", "2012",
     None, "Okinawa", "dx", None, None, None, None, None, None),
    # masked: by name and by legacy key (fixture gate) — no entry, no marker, no link
    ("7", None, "Tricholoma fictum", "Tricholoma", "Fictaceae", "Agaricales", "SPECIES",
     "HUMAN_OBSERVATION", "Agaricomycetes", "Basidiomycota", "26.3", "127.8", "4", "2011",
     None, "Okinawa", "dx", None, None, None, None, None, None),
    ("8", "9001", "Innocens alia", "Innocens", "Fictaceae", "Agaricales", "SPECIES",
     "HUMAN_OBSERVATION", "Agaricomycetes", "Basidiomycota", "26.3", "127.8", "4", "2011",
     None, "Okinawa", "dx", None, None, None, None, None, None),
]
XE_ROWS = [("1", "on dead Quercus trunk", "Quercus serrata | Fagus crenata", 1234.0, None, None),
           ("2", None, "Quercus serrata", 800.0, None, None),
           ("3", None, None, 900.0, None, None)]
EA_ROWS = [("5002", "Publicus bonum", "Fictaceae", 24.0, 121.0, 6, 3, "CX1")]

ENRICH_HEADER = ["sp_key", "name", "wikidata_qid", "wikipedia_ja", "wikipedia_en",
                 "photo_thumb_url", "photo_file_page", "photo_author", "photo_licence",
                 "photo_licence_url", "mhlw_url", "endemic_hosoya2016", "literature",
                 "mushroomexpert_url", "mushroomexpert_level"]
_LIT = json.dumps([{"doi": "10.1/paper", "title": "A Prosus paper", "year": 2020},
                   {"doi": "10.1/old", "title": "Older work", "year": 1999}])
ENRICH_ROWS = [
    # full row: free licence photo, MHLW, endemic, two papers, species-level ME link
    ("L5001", "Prosus lautus", "Q111", "https://ja.wikipedia.org/wiki/ハダイロガサ",
     "https://en.wikipedia.org/wiki/Prosus_lautus",
     "https://commons.wikimedia.org/w/thumb.php?f=prosus_lautus.jpg&w=96",
     "https://commons.wikimedia.org/wiki/File:prosus_lautus.jpg", "Anne Author", "CC BY-SA 4.0",
     "https://creativecommons.org/licenses/by-sa/4.0", "https://www.mhlw.go.jp/stf/x", "1",
     _LIT, "https://www.mushroomexpert.com/prosus_lautus.html", "species"),
    # non-free licence: the builder must drop the photo (spec section 2)
    ("L5003", "Prosus alter", "Q113", "", "", "", "https://commons.wikimedia.org/wiki/File:prosus_alter.jpg",
     "Somebody", "CC BY-NC-ND 3.0", "https://creativecommons.org/licenses/by-nc-nd/3.0", "", "", "", "", ""),
    # free licence but no author: incomplete photo, dropped too
    ("CX1", "Publicus bonum", "Q112", "", "", "", "", "", "CC BY 4.0",
     "https://creativecommons.org/licenses/by/4.0", "", "",
     json.dumps([{"doi": "10.1/bonum", "title": "Bonum notes", "year": 2018}]),
     "https://www.mushroomexpert.com/publicus.html", "genus"),
    # L5009 has no row at all: every external field stays empty, build stays green
]


def _enrichment_csv():
    import io
    buf = io.StringIO()
    w = csv.writer(buf, lineterminator="\n")
    w.writerow(ENRICH_HEADER)
    w.writerows(ENRICH_ROWS)
    return "# fixture enrichment (TASK-024)\n" + buf.getvalue()


ENRICHMENT_CSV = _enrichment_csv()


def build_fixture_db(path):
    c = duckdb.connect(path)
    c.execute(f"create table jp({JP_COLS})")
    c.executemany("insert into jp values (" + ",".join(["?"] * 23) + ")", JP_ROWS)
    c.execute("create table jp_extra(gbifID varchar, habitat varchar, assoc varchar, "
              "elevation double, dataGeneralizations varchar, informationWithheld varchar)")
    c.executemany("insert into jp_extra values (?,?,?,?,?,?)", XE_ROWS)
    c.execute('create table nb(species varchar, phylum varchar, "order" varchar, family varchar, '
              "genus varchar, class varchar, numberOfOccurrences varchar, iucnRedListCategory "
              "varchar, taxonRank varchar, taxonomicStatus varchar, speciesKey varchar)")
    c.executemany("insert into nb values (" + ",".join(["?"] * 11) + ")",
                  [("Publicus bonum", "Basidiomycota", "Agaricales", "Fictaceae", "Publicus",
                    "Agaricomycetes", "10", None, "SPECIES", "ACCEPTED", "5002"),
                   ("Nearby candidatus", "Basidiomycota", "Agaricales", "Candidaceae", "Nearby",
                    "Agaricomycetes", "30", None, "SPECIES", "ACCEPTED", "7009")])
    c.execute("create table mo(speciesKey varchar, countryCode varchar)")
    c.executemany("insert into mo values (?, ?)", [("5009", "JP")])
    c.execute("create table ea(speciesKey varchar, species varchar, family varchar, lat double, "
              "lon double, month int, n int, col_key varchar)")
    c.executemany("insert into ea values (?,?,?,?,?,?,?,?)", EA_ROWS)
    c.close()
    return path


def read_csv_rows(path):
    with open(path, newline="", encoding="utf-8") as f:
        return {r["sp_key"]: r for r in csv.DictReader(
            ln for ln in f if not ln.lstrip().startswith("#"))}


class SpeciesBundles(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp()
        cls.refs = ["--mask", os.path.join(FIX, "taxon_mask.csv"),
                    "--coverage", write_coverage(cls.tmp),
                    "--exceptions", os.path.join(FIX, "match_exceptions.csv"),
                    "--redlist", os.path.join(FIX, "redlist.json"),
                    "--col-mask-map", os.path.join(FIX, "col_mask_map.csv")]
        cls.db = build_fixture_db(os.path.join(cls.tmp, "species.duckdb"))
        with open(os.path.join(cls.tmp, "meta.json"), "w") as f:
            json.dump({"key": "dx", "doi": "10.9999/fake-doi", "totalRecords": 8,
                       "created": "2026-11-23T00:00:00Z"}, f)
        islands = os.path.join(cls.tmp, "islands.csv")
        with open(islands, "w") as f:
            f.write(ISLANDS_CSV)
        cls.enrich = os.path.join(cls.tmp, "enrichment.csv")
        with open(cls.enrich, "w") as f:
            f.write(ENRICHMENT_CSV)
        # the publish-dashboard.yml order: dashboard -> areas -> enrich -> species -> stamp
        cls.staging = os.path.join(cls.tmp, "staging")
        r = run_builder("build_dashboard_data.py", "--db", cls.db,
                        "--out", os.path.join(cls.staging, "dashboard.json"),
                        "--meta", os.path.join(cls.tmp, "meta.json"), *cls.refs)
        assert r.returncode == 0, r.stdout + r.stderr
        r = run_builder("build_areas.py", "--db", cls.db,
                        "--out", os.path.join(cls.staging, "areas"),
                        "--islands", islands, *cls.refs)
        assert r.returncode == 0, r.stdout + r.stderr
        cls.last_run = run_builder("build_species_bundles.py", "--db", cls.db,
                                   "--out", os.path.join(cls.staging, "species"),
                                   "--areas", os.path.join(cls.staging, "areas"),
                                   "--enrich", cls.enrich, *cls.refs)
        assert cls.last_run.returncode == 0, cls.last_run.stdout + cls.last_run.stderr
        r = run_builder("snapshot.py", cls.staging)
        assert r.returncode == 0, r.stdout + r.stderr
        with open(os.path.join(cls.staging, "species", "index.json")) as f:
            cls.index = json.load(f)

    @classmethod
    def validate(cls, path):
        return subprocess.run([sys.executable, os.path.join(REPO, "pipeline",
                                                            "validate_dashboard_publish.py"),
                               path, *cls.refs], capture_output=True, text=True, cwd=REPO)

    def genus(self, slug):
        with open(os.path.join(self.staging, "species", f"{slug}.json")) as f:
            return json.load(f)

    def entry(self, name):
        for slug in self.index["files"]:
            for e in self.genus(slug)["species"]:
                if e["name"] == name:
                    return e
        self.fail(f"no entry for {name}")

    # ---------- the green path ----------------------------------------------------------------

    def test_staging_with_species_validates(self):
        r = self.validate(self.staging)
        self.assertEqual(r.returncode, 0, r.stderr)

    def test_index_counts_and_hashes_cover_the_files(self):
        self.assertEqual(self.index["genera"], len(self.index["files"]))
        self.assertEqual(self.index["species"],
                         sum(v["species"] for v in self.index["files"].values()))
        h = hashlib.sha256()
        for slug in sorted(self.index["files"]):
            with open(os.path.join(self.staging, "species", f"{slug}.json"), "rb") as f:
                raw = f.read()
            meta = self.index["files"][slug]
            self.assertEqual(meta["sha256"], hashlib.sha256(raw).hexdigest())
            self.assertEqual(meta["bytes"], len(raw))
            self.assertEqual(meta["species"], len(json.loads(raw)["species"]))
            h.update(raw)
        self.assertEqual(self.index["snapshot"], h.hexdigest())

    def test_public_species_count_and_masked_absent(self):
        # 6 public species (lautus, alter, bonum, proximus... plus none other), the two
        # masked fixture taxa (Tricholoma fictum by name, key 9001) never appear
        self.assertEqual(self.index["species"], 4)
        blob = ""
        for slug in self.index["files"]:
            with open(os.path.join(self.staging, "species", f"{slug}.json")) as f:
                blob += f.read()
        for secret in ("Tricholoma", "fictum", "9001", "Innocens"):
            self.assertNotIn(secret, blob)

    def test_own_facts_aggregate_over_public_records(self):
        e = self.entry("Prosus lautus")
        self.assertEqual(e["records_by_tier"], {"s": 1, "d": 1, "o": 1})
        self.assertEqual(e["fruiting_months"][2], 1)      # March
        self.assertEqual(e["fruiting_months"][4], 1)      # May
        self.assertEqual(sum(e["fruiting_months"]), 2)    # August (MATERIAL_SAMPLE) excluded
        self.assertEqual(e["first_decade"], 2010)
        self.assertEqual(e["last_decade"], 2020)
        self.assertEqual(e["elevation_band"], [800, 1234])
        self.assertEqual(e["host_genera"], ["Quercus", "Fagus"])
        self.assertEqual(e["area_ids"], ["okisle"])
        self.assertNotIn("okisle", e["could_be_found_area_ids"])  # present species never candidate
        self.assertIsNone(e["key"])
        self.assertEqual(e["legacy_key"], "5001")
        self.assertFalse(e["east_asia"])
        self.assertEqual((e["family"], e["order"], e["class"], e["kind"]),
                         ("Fictaceae", "Agaricales", "Agaricomycetes", "m"))
        # the COL-keyed species carries the COL key and no legacy fallback
        b = self.entry("Publicus bonum")
        self.assertEqual(b["key"], "CX1")
        self.assertIsNone(b["legacy_key"])
        self.assertTrue(b["east_asia"])
        self.assertIn("okisle", b["area_ids"])
        # recorded only in the tile, but a candidate for the nearby island
        n = self.entry("Neighborus proximus")
        self.assertIn("t4028", n["area_ids"])
        self.assertIn("okisle", n["could_be_found_area_ids"])

    def test_external_block_from_enrichment(self):
        e = self.entry("Prosus lautus")
        self.assertEqual(e["name_ja"], "ハダイロガサ")
        self.assertEqual(e["external"]["wikidata_qid"], "Q111")
        self.assertEqual(e["external"]["wikipedia_ja"], "https://ja.wikipedia.org/wiki/ハダイロガサ")
        self.assertEqual(e["external"]["mhlw_url"], "https://www.mhlw.go.jp/stf/x")
        self.assertTrue(e["external"]["endemic_hosoya2016"])
        self.assertEqual([r["doi"] for r in e["external"]["literature"]],
                         ["10.1/paper", "10.1/old"])
        self.assertEqual(e["external"]["mushroomexpert_level"], "species")
        self.assertEqual(e["external"]["photo"],
                         {"thumb_url": "https://commons.wikimedia.org/w/thumb.php?f=prosus_lautus.jpg&w=96",
                          "file_page": "https://commons.wikimedia.org/wiki/File:prosus_lautus.jpg",
                          "author": "Anne Author", "licence": "CC BY-SA 4.0",
                          "licence_url": "https://creativecommons.org/licenses/by-sa/4.0"})
        # non-free licence and missing author both leave photo null
        self.assertIsNone(self.entry("Prosus alter")["external"]["photo"])
        self.assertIsNone(self.entry("Publicus bonum")["external"]["photo"])
        self.assertEqual(self.entry("Publicus bonum")["external"]["mushroomexpert_level"], "genus")

    def test_missing_enrichment_row_leaves_every_external_field_empty(self):
        x = self.entry("Neighborus proximus")["external"]
        self.assertEqual(x, {"wikidata_qid": None, "wikipedia_ja": None, "wikipedia_en": None,
                             "photo": None, "mhlw_url": None, "endemic_hosoya2016": False,
                             "literature": [], "mushroomexpert_url": None,
                             "mushroomexpert_level": None})
        self.assertIsNone(self.entry("Neighborus proximus")["name_ja"])

    def test_missing_enrichment_file_still_builds(self):
        out = os.path.join(tempfile.mkdtemp(prefix="sp-"), "species")
        r = run_builder("build_species_bundles.py", "--db", self.db, "--out", out,
                        "--areas", os.path.join(self.staging, "areas"),
                        "--enrich", os.path.join(self.tmp, "nope.csv"), *self.refs)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        with open(os.path.join(out, "index.json")) as f:
            self.assertEqual(json.load(f)["species"], 4)

    def test_rewrite_of_unchanged_genus_file_is_byte_identical(self):
        slug, path = "prosus", os.path.join(self.staging, "species", "prosus.json")
        before, mtime = open(path, "rb").read(), os.path.getmtime(path)
        r = run_builder("build_species_bundles.py", "--db", self.db,
                        "--out", os.path.join(self.staging, "species"),
                        "--areas", os.path.join(self.staging, "areas"),
                        "--enrich", self.enrich, *self.refs)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertEqual(open(path, "rb").read(), before)
        self.assertEqual(os.path.getmtime(path), mtime)   # not even rewritten

    # ---------- the reject matrix (validator) --------------------------------------------------

    def reject(self, mutate, *needles):
        path = os.path.join(tempfile.mkdtemp(prefix="rej-"), "staging")
        shutil.copytree(self.staging, path)
        mutate(path)
        r = self.validate(path)
        self.assertNotEqual(r.returncode, 0, f"unexpectedly publishable:\n{r.stdout}")
        for needle in needles:
            self.assertIn(needle, r.stderr, f"{needle!r} missing from:\n{r.stderr}")

    def edit(self, path, rel, fn):
        p = os.path.join(path, rel)
        with open(p) as f:
            d = json.load(f)
        fn(d)
        with open(p, "w") as f:
            json.dump(d, f, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
            f.write("\n")

    def test_tampered_genus_file_rejects(self):
        def tamper(p):
            with open(os.path.join(p, "species", "prosus.json"), "a") as f:
                f.write(" ")
        self.reject(tamper, "does not match its index entry")

    def test_index_digest_mismatch_rejects(self):
        def snap(p):
            self.edit(p, "species/index.json", lambda d: d.update(snapshot="0" * 64))
        self.reject(snap, "species digest")

    def test_masked_name_or_key_in_species_rejects(self):
        def masked_name(p):
            self.edit(p, "species/prosus.json",
                      lambda d: d["species"].append({**d["species"][0], "name": "Tricholoma fictum",
                                                     "key": None, "legacy_key": None}))
        self.reject(masked_name, "protected taxon")

        def masked_key(p):
            self.edit(p, "species/prosus.json",
                      lambda d: d["species"][0].update(legacy_key="9001"))
        self.reject(masked_key, "masked legacy key")

    def test_incomplete_photo_rejects(self):
        def no_author(p):
            self.edit(p, "species/prosus.json",
                      lambda d: next(e for e in d["species"]
                                     if e["external"]["photo"])["external"]["photo"].pop("author"))
        self.reject(no_author, "photo needs thumb_url, file_page, author, licence AND "
                               "licence_url")

    def test_bad_entry_shape_rejects(self):
        def bad_tier(p):
            self.edit(p, "species/prosus.json",
                      lambda d: d["species"][0].update(records_by_tier={"x": 1}))
        self.reject(bad_tier, "mistyped item-7 field")

        def both_keys(p):
            self.edit(p, "species/prosus.json",
                      lambda d: d["species"][0].update(key="CX9", legacy_key="5001"))
        self.reject(both_keys, "legacy_key with key set")

    def test_index_count_mismatch_rejects(self):
        def bump(p):
            self.edit(p, "species/index.json", lambda d: d.update(species=d["species"] + 1))
        self.reject(bump, "the summed per-file counts")

    def test_stray_species_file_rejects(self):
        def stray(p):
            with open(os.path.join(p, "species", "stray.json"), "w") as f:
                f.write("{}")
        self.reject(stray, "stray file")

    def test_unknown_area_id_rejects(self):
        def bad_area(p):
            self.edit(p, "species/prosus.json",
                      lambda d: d["species"][0].update(area_ids=["nowhere"]))
        self.reject(bad_area, "not in the area index")

    # ---------- the workflow: enrich + species build + ship -------------------------------------

    def test_workflow_builds_and_ships_species(self):
        with open(WORKFLOW) as f:
            text = f.read()
        for marker in ("pipeline/enrich.py", "build_species_bundles.py",
                       "staging/species", "cp -r staging/species",
                       "enrichment-retrieved.txt", "actions/cache/restore@v4",
                       "actions/cache/save@v4"):
            self.assertIn(marker, text)
        self.assertLess(text.index("build_areas.py --out staging"), text.index("pipeline/enrich.py"))
        self.assertLess(text.index("pipeline/enrich.py"),
                        text.index("build_species_bundles.py"))
        self.assertLess(text.index("build_species_bundles.py"),
                        text.index("validate_dashboard_publish.py staging"))
        self.assertLess(text.index("validate_dashboard_publish.py staging"),
                        text.index("cp -r staging/species"))


class EnrichJoin(unittest.TestCase):
    """pipeline/enrich.py over fixture source caches: the join itself, graceful
    degradation on missing caches, and the last-retrieved fallback."""

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp()
        cls.refs = ["--mask", os.path.join(FIX, "taxon_mask.csv"),
                    "--coverage", write_coverage(cls.tmp),
                    "--exceptions", os.path.join(FIX, "match_exceptions.csv"),
                    "--redlist", os.path.join(FIX, "redlist.json"),
                    "--col-mask-map", os.path.join(FIX, "col_mask_map.csv")]
        cls.db = build_fixture_db(os.path.join(cls.tmp, "enrich.duckdb"))

        wd = os.path.join(cls.tmp, "wikidata")
        os.makedirs(wd)
        with open(os.path.join(wd, "species_wikidata.json"), "w") as f:
            json.dump({"L5001": {"item": True, "qid": "Q111", "jawp": "ハダイロガサ",
                                 "enwp": "Prosus_lautus", "p18": "Prosus lautus.jpg",
                                 "licence": "CC BY-SA"},
                       "CX1": {"item": True, "qid": "Q112", "jawp": None, "enwp": None,
                               "p18": "Publicus.jpg", "licence": None},
                       "L5003": {"item": True, "qid": "Q113", "jawp": None, "enwp": None,
                                 "p18": "Prosus alter.jpg", "licence": None}}, f)
        def commons_page(title, artist, licence, licence_url):
            return {"title": title, "imageinfo": [{"extmetadata": {
                "Artist": {"value": artist}, "LicenseShortName": {"value": licence},
                "LicenseUrl": {"value": licence_url}}}]}

        with open(os.path.join(wd, "commons-imageinfo-001.json"), "w") as f:
            json.dump({"query": {"pages": {str(i): p for i, p in enumerate([
                commons_page("File:Prosus lautus.jpg",
                             '<a href="https://flickr.com/x">Anne Author</a>',
                             "CC BY-SA 4.0", "https://creativecommons.org/licenses/by-sa/4.0"),
                commons_page("File:Publicus.jpg", "Somebody", "CC BY-NC-ND 3.0",
                             "https://creativecommons.org/licenses/by-nc-nd/3.0"),
                commons_page("File:Prosus alter.jpg", "Other", "CC BY 4.0",
                             "https://creativecommons.org/licenses/by/4.0")], 1)}}}, f)
        cls.wd = wd

        my = os.path.join(cls.tmp, "mycoscience")
        os.makedirs(my)
        with open(os.path.join(my, "matches.json"), "w") as f:
            json.dump({"per_species": {}, "per_article": [
                {"src": "crossref", "id": "10.1/paper", "year": 2020,
                 "title": "A Prosus paper", "species": ["prosus lautus"], "genera": []},
                {"src": "crossref", "id": "10.1/old", "year": 1999,
                 "title": "Older work", "species": ["prosus lautus"], "genera": []},
                {"src": "jstage", "id": "https://www.jstage.jst.go.jp/x", "year": None,
                 "title": "Recent J-STAGE note", "species": ["prosus lautus"], "genera": []},
                # genus-only hit: never attaches to a species
                {"src": "crossref", "id": "10.1/genus-only", "year": 2021,
                 "title": "Genus only", "species": [], "genera": ["publicus"]}]}, f)
        cls.my = my

        cls.mhlw = os.path.join(cls.tmp, "mhlw.csv")
        with open(cls.mhlw, "w") as f:
            f.write("# fixture\ncol_key,scientific_name,url\n"
                    "L5001,Prosus lautus,https://www.mhlw.go.jp/stf/x\n")
        cls.hosoya = os.path.join(cls.tmp, "hosoya.csv")
        with open(cls.hosoya, "w") as f:
            f.write("# fixture\ncol_key,scientific_name,source_page\n"
                    "L5001,Prosus lautus,p.81\n")
        cls.me = os.path.join(cls.tmp, "me.csv")
        with open(cls.me, "w") as f:
            f.write("# fixture\nsp_key,level,url\n"
                    "L5001,species,https://www.mushroomexpert.com/prosus_lautus.html\n"
                    "CX1,genus,https://www.mushroomexpert.com/publicus.html\n")

    def run_enrich(self, out, **kw):
        args = ["--db", self.db, "--out", out, "--sidecar", out + ".retrieved",
                "--wikidata-cache", kw.get("wd", self.wd), "--mycoscience-cache", kw.get("my", self.my),
                "--mhlw", kw.get("mhlw", self.mhlw), "--hosoya", kw.get("hosoya", self.hosoya),
                "--mushroomexpert", kw.get("me", self.me), *self.refs]
        return run_builder("enrich.py", *args), read_csv_rows(out)

    def test_join_of_all_sources(self):
        out = os.path.join(self.tmp, "joined.csv")
        r, rows = self.run_enrich(out)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertEqual(set(rows), {"L5001", "L5003", "CX1", "L5009"})   # public species only
        row = rows["L5001"]
        self.assertEqual(row["wikidata_qid"], "Q111")
        self.assertEqual(row["wikipedia_ja"],
                         "https://ja.wikipedia.org/wiki/" + urllib.parse.quote("ハダイロガサ"))
        self.assertEqual(row["wikipedia_en"], "https://en.wikipedia.org/wiki/Prosus_lautus")
        self.assertEqual(row["photo_thumb_url"],
                         "https://commons.wikimedia.org/w/thumb.php?f=Prosus%20lautus.jpg&w=96")
        self.assertEqual(row["photo_file_page"],
                         "https://commons.wikimedia.org/wiki/File:Prosus_lautus.jpg")
        self.assertEqual(row["photo_author"], "Anne Author")          # HTML credit cleaned
        self.assertEqual(row["photo_licence_url"],
                         "https://creativecommons.org/licenses/by-sa/4.0")
        self.assertEqual(row["mhlw_url"], "https://www.mhlw.go.jp/stf/x")
        self.assertEqual(row["endemic_hosoya2016"], "1")
        lit = json.loads(row["literature"])
        self.assertEqual([(x["doi"], x["year"]) for x in lit],
                         [("10.1/paper", 2020), ("10.1/old", 1999),
                          ("https://www.jstage.jst.go.jp/x", None)])
        self.assertEqual(row["mushroomexpert_level"], "species")
        # non-free licence photo dropped; free photo for a species with one
        self.assertEqual(rows["CX1"]["photo_thumb_url"], "")
        self.assertNotEqual(rows["L5003"]["photo_thumb_url"], "")
        # no source row at all: empty everywhere, and the sidecar was written
        self.assertEqual({v for v in rows["L5009"].values() if v != ""}, {"L5009", "Neighborus proximus"})
        self.assertTrue(os.path.exists(out + ".retrieved"))

    def test_literature_caps_at_five_newest_first(self):
        my = os.path.join(self.tmp, "my-many")
        os.makedirs(my, exist_ok=True)
        arts = [{"src": "crossref", "id": f"10.1/{i}", "year": 1990 + i, "title": f"Paper {i}",
                 "species": ["prosus lautus"], "genera": []} for i in range(8)]
        with open(os.path.join(my, "matches.json"), "w") as f:
            json.dump({"per_species": {}, "per_article": arts}, f)
        out = os.path.join(self.tmp, "capped.csv")
        r, rows = self.run_enrich(out, my=my)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        lit = json.loads(rows["L5001"]["literature"])
        self.assertEqual(len(lit), 5)
        self.assertEqual([x["year"] for x in lit], [1997, 1996, 1995, 1994, 1993])

    def test_missing_caches_never_block_and_keep_last_retrieved_values(self):
        empty = os.path.join(self.tmp, "empty")
        os.makedirs(empty, exist_ok=True)
        # nothing anywhere, no previous csv: all-empty rows, exit 0 (warning only)
        fresh = os.path.join(self.tmp, "fresh.csv")
        r, rows = self.run_enrich(fresh, wd=empty, my=empty, mhlw=fresh + ".nope",
                                  hosoya=fresh + ".nope", me=fresh + ".nope")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertEqual(rows["L5001"]["wikidata_qid"], "")
        self.assertEqual(rows["L5001"]["literature"], "")
        # a previous csv survives a cache-less rerun: the last retrieval stands
        keep = os.path.join(self.tmp, "keep.csv")
        r, rows = self.run_enrich(keep)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertEqual(rows["L5001"]["wikidata_qid"], "Q111")
        r, rows = self.run_enrich(keep, wd=empty, my=empty)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertEqual(rows["L5001"]["wikidata_qid"], "Q111")
        self.assertNotEqual(rows["L5001"]["photo_thumb_url"], "")
        self.assertNotEqual(rows["L5001"]["literature"], "")


if __name__ == "__main__":
    unittest.main()
