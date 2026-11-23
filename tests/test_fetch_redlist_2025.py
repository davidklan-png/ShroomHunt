"""TASK-020: the 2025 Red List input — pdftotext-row parser, colmap-first
matcher with live fallback, the pure/union transition rule, and the JSON shape
redlist_facts()/load_gate() consume. Fictional taxa only (test_public_taxa
fixtures); nothing reads data/raw/ and no pdftotext/pypdf import happens.
"""
import csv, json, os, sys, tempfile, unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import duckdb

from pipeline import fetch_redlist_2025 as fr
from pipeline.col_xr import load_map
from test_col_xr import write_tsv_gz
from test_public_taxa import FIX, gate_with, write_coverage

# A miniature of the real pdftotext -layout output (title, column header, rows,
# page footer). FU0002 carries an infraspecific epithet, FU0003 two scientific
# names in one row, FU0005's Japanese name sits one space from its name.
TEXT = """
                               環境省第５次レッドリスト（菌類）

掲載No.    レッドリストカテゴリー   和名                      学名
FU0001   絶滅（EX）        ハハジマモリノカサ               Agaricus hahashimensis
FU0002   情報不足（DD）      ナガエノホコリタケ              Tulostoma fimbriatum var. campestre
FU0003   絶滅危惧ⅠB類（EN）   ケガワタケ属の１種               Lentinus lamelliporus    Neoalbatrellus yasudae
FU0004   準絶滅危惧（NT）     マツタケ                    Tricholoma matsutake
FU0005   絶滅危惧Ⅱ類（VU）    ムニンヒメサカズキタケ Clitocybe castaneofloccosa

                                       1 / 1 ページ
"""

# (gbif:ID, rank, gbif:scientificName, kingdom, col:ID, status, col:scientificName,
#  col:acceptedID, col:acceptedScientificName, family, genus)
XWALK = [
    ("8001", "SPECIES", "Agaricus hahashimensis", "Fungi", "CRL1", "accepted", "Agaricus hahashimensis", "", "", "Rareaceae", "Agaricus"),
    ("8002", "SPECIES", "Pseudo fimbriatus", "Fungi", "CRL9", "synonym", "Pseudo fimbriatus", "CRL1", "Agaricus hahashimensis", "Rareaceae", "Agaricus"),
    ("8003", "SPECIES", "Tricholoma matsutake", "Fungi", "CRL2", "accepted", "Tricholoma matsutake", "", "", "Fictaceae", "Tricholoma"),
    ("8004", "SPECIES", "Matsutake vetus", "Fungi", "CRL10", "synonym", "Matsutake vetus", "CRL2", "Tricholoma matsutake", "Fictaceae", "Tricholoma"),
    ("8005", "SPECIES", "Matsutake vetus", "Fungi", "CRL11", "synonym", "Matsutake vetus", "CRL3", "Matsutake aliud", "Fictaceae", "Tricholoma"),
    ("8006", "SPECIES", "Lentinus lamelliporus", "Fungi", "CRL4", "accepted", "Lentinus lamelliporus", "", "", "Fictaceae", "Lentinus"),
    ("8007", "SPECIES", "Neoalbatrellus yasudae", "Fungi", "CRL5", "accepted", "Neoalbatrellus yasudae", "", "", "Fictaceae", "Neoalbatrellus"),
    ("8008", "SPECIES", "Liveus servus", "Fungi", "CRL6", "accepted", "Liveus servus", "", "", "Fictaceae", "Liveus"),
]


class Parser(unittest.TestCase):
    def test_rows_names_and_categories(self):
        rows, stats = fr.parse_rows(TEXT)
        self.assertEqual([r["no"] for r in rows], [1, 2, 3, 3, 4, 5])   # FU0003 has two names
        by_name = {r["scientific_name"]: r for r in rows}
        self.assertEqual(by_name["Agaricus hahashimensis"]["category_code"], "EX")
        self.assertEqual(by_name["Tulostoma fimbriatum var. campestre"]["category_ja"], "情報不足（DD）")
        self.assertEqual(by_name["Tulostoma fimbriatum var. campestre"]["name_ja"], "ナガエノホコリタケ")
        self.assertEqual(by_name["Neoalbatrellus yasudae"]["name_ja"], "ケガワタケ属の１種")  # same row, 2nd name
        # a Japanese name one space from the scientific name still splits
        self.assertEqual(by_name["Clitocybe castaneofloccosa"]["name_ja"], "ムニンヒメサカズキタケ")

    def test_parse_is_complete_only_without_gaps_or_leftovers(self):
        _, stats = fr.parse_rows(TEXT)
        self.assertEqual((stats["rows"], stats["gaps"], stats["other"]), (6, [], []))
        self.assertTrue(stats["complete"])
        # a numbering gap (or duplicate) means rows were lost — flagged, not shipped silently
        gappy = TEXT.replace("FU0003", "FU0004")
        _, stats = fr.parse_rows(gappy)
        self.assertEqual(stats["gaps"], [(2, 4), (4, 4)])
        self.assertFalse(stats["complete"])

    def test_parsed_csv_shape(self):
        rows, _ = fr.parse_rows(TEXT)
        path = os.path.join(tempfile.mkdtemp(), "redlist_2025_parsed.csv")
        fr.write_parsed_csv(rows, path)
        with open(path, encoding="utf-8") as f:
            lines = [ln for ln in f if not ln.startswith("#")]
        self.assertEqual(lines[0].strip(), "category_ja,category_code,name_ja,scientific_name")
        self.assertEqual(len(lines), 7)                                 # header + 6 entries


class Matcher(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.con = duckdb.connect()
        load_map(self.con, write_tsv_gz(os.path.join(self.tmp, "col.tsv.gz"), XWALK))
        self.calls = []

    def tearDown(self):
        self.con.close()

    def live(self, answer):
        def _live(name):
            self.calls.append(name)
            return answer
        return _live

    def match(self, names, answer=None):
        return fr.match_species(names, self.con, live=self.live(answer), sleep=0)

    def test_exact_colmap_match_by_gbif_name(self):
        (name, key, mt, col), = self.match(["Agaricus hahashimensis"])
        self.assertEqual((name, key, mt, col), ("Agaricus hahashimensis", "8001", "EXACT", "CRL1"))
        self.assertEqual(self.calls, [])                       # no API call when colmap resolves

    def test_accepted_name_hit_prefers_the_accepted_usage_key(self):
        # "pseudo fimbriatus" reaches CRL1 via its accepted name too; the legacy key
        # recorded is the accepted-status row that actually carries the name
        (name, key, mt, col), = self.match(["Pseudo fimbriatus"])
        self.assertEqual((key, mt, col), ("8002", "EXACT", "CRL1"))

    def test_two_accepted_taxa_is_ambiguous_without_a_key(self):
        (name, key, mt, col), = self.match(["Matsutake vetus"])
        self.assertEqual((key, mt, col), (None, "AMBIGUOUS", None))

    def test_live_fallback_maps_the_backbone_key_through_colmap(self):
        (name, key, mt, col), = self.match(["Tulostoma fimbriatum var. campestre"],
                                           answer={"speciesKey": 8008, "matchType": "FUZZY"})
        self.assertEqual((key, mt, col), ("8008", "FUZZY", "CRL6"))
        self.assertEqual(self.calls, ["Tulostoma fimbriatum var. campestre"])

    def test_no_match_anywhere_is_none(self):
        for answer in (None, {"speciesKey": None, "matchType": "NONE"}):
            self.assertEqual(self.match(["Neminem novi"], answer=answer)[0][1:], (None, "NONE", None))


class Transition(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.old = os.path.join(self.tmp, "redlist_fungi_jp.json")
        with open(self.old, "w") as f:
            json.dump({"dataset": "old", "doi": fr.OLD_DOI, "fetched": "2020-01-01", "species": [
                {"name": "Agaricus hahashimensis", "status": "DD", "speciesKey": 8001, "matchType": "EXACT"},
                {"name": "Vetus japonicus", "status": "EN", "speciesKey": 9001, "matchType": "EXACT"}]}, f)

    def pure(self, mt, key=8003):
        return [{"name": "Agaricus hahashimensis", "status": "EX", "speciesKey": 8001, "matchType": "EXACT"},
                {"name": "Tricholoma matsutake", "status": "NT", "speciesKey": key, "matchType": mt}]

    def test_all_matched_is_pure_2025(self):
        main = os.path.join(self.tmp, "main.json")
        decision = fr.transition(self.pure("EXACT"), self.old, main)
        self.assertEqual(decision, "pure-2025")
        # AMBIGUOUS counts as matched: several accepted taxa still mask by name
        self.assertEqual(fr.transition(self.pure("AMBIGUOUS", key=None), self.old, main), "pure-2025")
        with open(main) as f:
            self.assertEqual([s["name"] for s in json.load(f)["species"]], ["Agaricus hahashimensis", "Tricholoma matsutake"])
        # the 2020 file is archived untouched for provenance
        archive = os.path.join(self.tmp, "redlist_fungi_jp_2020.json")
        with open(archive) as f:
            self.assertEqual([s["name"] for s in json.load(f)["species"]], ["Agaricus hahashimensis", "Vetus japonicus"])

    def test_any_none_unions_with_2020(self):
        main = os.path.join(self.tmp, "main.json")
        decision = fr.transition(self.pure("NONE", key=None), self.old, main)
        self.assertEqual(decision, "union-2020+2025")
        with open(main) as f:
            sp = {s["name"]: s for s in json.load(f)["species"]}
        self.assertEqual(sorted(sp), ["Agaricus hahashimensis", "Tricholoma matsutake", "Vetus japonicus"])
        # 2025 status wins; an unmatched 2025 entry keeps the 2020 key (fail-closed)
        self.assertEqual(sp["Vetus japonicus"]["speciesKey"], 9001)
        self.assertEqual((sp["Agaricus hahashimensis"]["status"], sp["Agaricus hahashimensis"]["speciesKey"]), ("EX", 8001))

    def test_union_requires_the_2020_file(self):
        main = os.path.join(self.tmp, "main.json")
        os.remove(self.old)
        with self.assertRaises(SystemExit):
            fr.transition(self.pure("NONE", key=None), self.old, main)


class Shape(unittest.TestCase):
    """A generated 2025 file loads through redlist_facts()/load_gate(); the
    match_exceptions aliases keep applying alongside the new list."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()

    def test_fixture_2025_json_loads_and_masks(self):
        path = os.path.join(self.tmp, "redlist_fungi_jp_2025.json")
        fr.write_json(path, [
            {"name": "Agaricus hahashimensis", "status": "EX", "speciesKey": 8001, "matchType": "EXACT"},
            {"name": "Tricholoma matsutake", "status": "NT", "speciesKey": None, "matchType": "NONE"}])
        from pipeline.public_taxa import redlist_facts
        _, n_names, n_matched, n_ambiguous, species = redlist_facts(path)
        self.assertEqual((n_names, n_matched, n_ambiguous), (2, 1, 0))
        gate = gate_with(self.tmp, redlist=path, coverage=write_coverage(self.tmp, redlist=path))
        self.assertEqual(gate.classify(species="Agaricus hahashimensis"), "name")
        self.assertEqual(gate.classify(species="Agaricus hahashimensis", species_key=8001), "key")
        # fail-closed: a NONE entry keeps masking by name, it just carries no key
        self.assertEqual(gate.classify(species="Tricholoma matsutake"), "name")
        # the exceptions-file alias still masks with the 2025 list in place
        self.assertEqual(gate.classify(species="Boletus fictus"), "name")


if __name__ == "__main__":
    unittest.main()
