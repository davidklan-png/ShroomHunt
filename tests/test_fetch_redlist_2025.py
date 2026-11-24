"""TASK-020: the 2025 Red List input — pdftotext-row parser, colmap-first
matcher with live fallback, the pure/union transition rule, and the JSON shape
redlist_facts()/load_gate() consume. TASK-026: the lichen list joins it (both
lists parsed and checked each on its own, `list` on every entry).
Fictional taxa only (test_public_taxa fixtures); nothing reads data/raw/ and
no pdftotext/pypdf import happens.
"""
import csv, json, os, sys, tempfile, unittest
from collections import Counter
from unittest import mock

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

# The lichen list (TASK-026): same chrome, LI numbering from 0001. Invented names.
# LI0001's Japanese name swallows the column gap, LI0002 carries ë, LI0003 a
# variety; LI0004 is the name the faked matcher leaves unmatched (NONE).
LICHEN_TEXT = """
                               環境省第５次レッドリスト（地衣類）

掲載No.    レッドリストカテゴリー   和名                          学名
LI0001   絶滅（EX）        ナガナガナガナガテストゴケア Lichenifictus primus
LI0002   絶滅危惧ⅠＡ類（CR）   テストゴケイ                   Lichenifictus fictënsis
LI0003   絶滅危惧ⅠB類（EN）   テストゴケウ                   Lichenifictus secundus var. tertius
LI0004   情報不足（DD）      テストゴケエ                   Lichenifictus nullus

                                       1 / 1 ページ
"""


def page(title, rows):
    """A one-page document in the shape above around the given row lines."""
    return (f"\n                               環境省第５次レッドリスト（{title}）\n\n"
            "掲載No.    レッドリストカテゴリー   和名                          学名\n"
            + "\n".join(rows) + "\n\n                                       1 / 1 ページ\n")


def padded(text, tag, title, n):
    """The fixture page `text` with generated invented rows (same tag) up to 掲載No. n."""
    rows = [ln.strip() for ln in text.splitlines() if ln.startswith(tag)]
    genus = "Lichenifictus" if tag == "LI" else "Fictofungus"
    rows += [f"{tag}{i:04d}   情報不足（DD）      テストゴケ{i:03d}               {genus} numerus{i:03d}"
             for i in range(len(rows) + 1, n + 1)]
    return page(title, rows)

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

    def test_incomplete_or_thin_parse_fails_closed(self):
        # PR #3 review P1: the 2020 union cannot protect newly listed 2025 taxa a
        # broken parse omits, so sanity_check must block before anything is written.
        with self.assertRaises(SystemExit):
            fr.sanity_check({"rows": 6, "gaps": [(2, 4)], "other": [], "complete": False}, "x.pdf")
        with self.assertRaises(SystemExit):
            fr.sanity_check({"rows": 6, "gaps": [], "other": ["leftover line"], "complete": False}, "x.pdf")
        with self.assertRaises(SystemExit):
            fr.sanity_check({"rows": 3, "gaps": [], "other": [], "complete": True}, "x.pdf")
        old_min, fr.MIN_EXPECTED_ROWS = fr.MIN_EXPECTED_ROWS, 6
        try:
            fr.sanity_check({"rows": 6, "gaps": [], "other": [], "complete": True}, "x.pdf")  # passes quietly
        finally:
            fr.MIN_EXPECTED_ROWS = old_min

    def test_lichen_page_parses_on_its_own_numbering(self):
        rows, stats = fr.parse_rows(LICHEN_TEXT)
        self.assertEqual((stats["rows"], stats["gaps"], stats["other"], stats["lists"]),
                         (4, [], [], ["lichens"]))
        self.assertTrue(stats["complete"])
        self.assertEqual([(r["no"], r["list"]) for r in rows], [(1, "lichens"), (2, "lichens"),
                                                                (3, "lichens"), (4, "lichens")])
        by_name = {r["scientific_name"]: r for r in rows}
        # a long 和名 that swallowed the column gap splits at the first Latin letter
        self.assertEqual((by_name["Lichenifictus primus"]["name_ja"], by_name["Lichenifictus primus"]["category_code"]),
                         ("ナガナガナガナガテストゴケア", "EX"))
        self.assertEqual(by_name["Lichenifictus fictënsis"]["category_ja"], "絶滅危惧ⅠＡ類（CR）")  # ë kept
        self.assertEqual(by_name["Lichenifictus secundus var. tertius"]["name_ja"], "テストゴケウ")
        # an LI gap is flagged like a FU one (each list counts from 0001), and a row
        # with any other prefix is an unparsed leftover, never an entry
        _, gappy = fr.parse_rows(LICHEN_TEXT.replace("LI0002", "LI0003"))
        self.assertEqual((gappy["gaps"], gappy["complete"]), ([(1, 3), (3, 3)], False))
        _, other = fr.parse_rows(LICHEN_TEXT.replace("LI0001", "XX0001"))
        self.assertEqual((other["rows"], len(other["other"]), other["complete"]), (3, 1, False))

    def test_each_list_has_its_own_minimum_and_identity(self):
        lichen = {"gaps": [], "other": [], "complete": True, "lists": ["lichens"]}
        with self.assertRaises(SystemExit):                 # a short lichen parse blocks ...
            fr.sanity_check(dict(lichen, rows=110), "x.pdf", fr.MIN_LICHEN_ROWS, "lichens")
        with self.assertRaises(SystemExit):                 # ... as does another list's rows
            fr.sanity_check(dict(lichen, rows=130, lists=["fungi"]), "x.pdf", fr.MIN_LICHEN_ROWS, "lichens")
        fr.sanity_check(dict(lichen, rows=fr.MIN_LICHEN_ROWS), "x.pdf", fr.MIN_LICHEN_ROWS, "lichens")

    def test_parsed_csv_shape(self):
        rows, _ = fr.parse_rows(TEXT)
        path = os.path.join(tempfile.mkdtemp(), "redlist_2025_parsed.csv")
        fr.write_parsed_csv(rows, path)
        with open(path, encoding="utf-8") as f:
            lines = [ln for ln in f if not ln.startswith("#")]
        self.assertEqual(lines[0].strip(), "list,category_ja,category_code,name_ja,scientific_name")
        self.assertEqual(len(lines), 7)                                 # header + 6 entries
        self.assertTrue(all(ln.startswith("fungi,") for ln in lines[1:]))


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
        self.assertEqual(sp["Vetus japonicus"]["list"], "fungi-2020")    # a 2020-only entry is tagged too
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


class BothLists(unittest.TestCase):
    """main() over both PDFs with the downloads faked (no network): a partial
    parse of either list blocks every write; the union JSON carries `list` on
    every entry, loads through redlist_facts()/load_gate() and masks lichens."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.out = os.path.join(self.tmp, "redlist")
        os.makedirs(self.out)
        self.parsed = os.path.join(self.tmp, "parsed.csv")
        self.main = os.path.join(self.out, fr.MAIN)
        self.coverage = os.path.join(self.tmp, "coverage.csv")
        open(self.coverage, "w").close()
        with open(os.path.join(self.out, fr.OLD), "w") as f:      # the union fallback input
            json.dump({"dataset": "old", "doi": fr.OLD_DOI, "species": [
                {"name": "Vetus japonicus", "status": "EN", "speciesKey": 9001, "matchType": "EXACT"}]}, f)

    def pages(self, fu=fr.MIN_EXPECTED_ROWS, li=fr.MIN_LICHEN_ROWS):
        return padded(TEXT, "FU", "菌類", fu), padded(LICHEN_TEXT, "LI", "地衣類", li)

    def run_main(self, fungi, lichens):
        """main() with both PDFs replaced by the page texts and match_species faked
        (a made-up EXACT key; names with " nullus" stay NONE)."""
        texts = {"05-5thredlist-fungi.pdf": fungi, "04-5thredlist-lichens.pdf": lichens}

        def download(url, path, force=False):
            with open(path, "wb") as f:
                f.write(b"%PDF")
            return path

        def extract_text(pdf, txt_path):
            with open(txt_path, "w", encoding="utf-8") as f:
                f.write(texts[os.path.basename(pdf)])
            return txt_path

        def matched(names, con, sleep=1.0):
            return [(n, None if " nullus" in n else 7000 + i,
                     "NONE" if " nullus" in n else "EXACT", None) for i, n in enumerate(names)]

        tsv = write_tsv_gz(os.path.join(self.tmp, "col.tsv.gz"), [])
        with mock.patch.multiple(fr, download=download, extract_text=extract_text, match_species=matched):
            fr.main(["--out-dir", self.out, "--parsed", self.parsed, "--map", tsv,
                     "--coverage", self.coverage, "--diff", ""])

    def test_partial_parse_of_either_list_blocks_every_write(self):
        fu, li = self.pages()
        cases = {"LI gap": (fu, li.replace("LI0007   ", "LI0008   ")),
                 "FU gap": (fu.replace("FU0006   ", "FU0007   "), li),
                 "short LI": (fu, self.pages(li=10)[1]),
                 "FU rows served for LI": (fu, padded(TEXT, "FU", "地衣類", fr.MIN_LICHEN_ROWS + 10))}
        for what, (fungi, lichens) in cases.items():
            with self.subTest(what):
                with self.assertRaises(SystemExit):
                    self.run_main(fungi, lichens)
                for p in (self.parsed, self.main, os.path.join(self.out, fr.PURE)):
                    self.assertFalse(os.path.exists(p), p)

    def test_union_json_carries_list_and_masks_lichens(self):
        self.run_main(*self.pages())
        with open(self.main, encoding="utf-8") as f:
            d = json.load(f)
        sp = {s["name"]: s for s in d["species"]}
        # header: both sources; every entry tagged, the 2020-only union entry included
        self.assertEqual(d["dataset"], fr.URLS)
        self.assertEqual(len(sp), 101 + fr.MIN_LICHEN_ROWS + 1)       # fungi names + lichen names + 2020-only
        self.assertEqual({s["list"] for s in sp.values()}, {"fungi", "lichens", "fungi-2020"})
        self.assertEqual((sp["Vetus japonicus"]["list"], sp["Agaricus hahashimensis"]["list"],
                          sp["Lichenifictus primus"]["list"]), ("fungi-2020", "fungi", "lichens"))
        self.assertEqual((sp["Lichenifictus nullus"]["speciesKey"], sp["Lichenifictus nullus"]["matchType"]),
                         (None, "NONE"))
        with open(self.parsed, encoding="utf-8") as f:
            rows = list(csv.DictReader(ln for ln in f if not ln.startswith("#")))
        self.assertEqual(list(rows[0]), ["list", "category_ja", "category_code", "name_ja", "scientific_name"])
        self.assertEqual(Counter(r["list"] for r in rows), {"fungi": 101, "lichens": fr.MIN_LICHEN_ROWS})
        # through redlist_facts() and the gate; main() appended the matching coverage row
        from pipeline.public_taxa import redlist_facts
        sha, n_names, n_matched, n_ambiguous, _ = redlist_facts(self.main)
        with open(self.coverage, encoding="utf-8") as f:
            self.assertIn(sha, f.read())
        gate = gate_with(self.tmp, redlist=self.main, coverage=write_coverage(self.tmp, redlist=self.main))
        self.assertEqual((gate.redlist_names, n_matched, n_ambiguous), (len(sp), len(sp) - 1, 0))
        # a red-listed lichen masks by key ...
        key = sp["Lichenifictus primus"]["speciesKey"]
        self.assertIsInstance(key, int)
        self.assertEqual(gate.classify(species="Alius nomens", species_key=key), "key")
        # ... and by name only (NONE, no key), infraspecific form included
        self.assertEqual(gate.classify(species="Lichenifictus nullus"), "name")
        self.assertEqual(gate.classify(species="Lichenifictus secundus var. tertius"), "name")
        # species level only: the genus and another species of it stay public
        self.assertIsNone(gate.classify(genus="Lichenifictus", taxon_rank="GENUS"))
        self.assertIsNone(gate.classify(species="Lichenifictus publicus", genus="Lichenifictus",
                                        taxon_rank="SPECIES"))


if __name__ == "__main__":
    unittest.main()
