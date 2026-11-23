"""TASK-022 item 2: the MHLW parsers — キノコ-table extraction (including the
row that carries the rowspan <th>), risk-profile title → binomial, and the
public-only matcher with its live-fallback seam. Fictional taxa; no network.
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import duckdb

from pipeline.sources import mhlw
from test_col_xr import write_tsv_gz

INDEX = """<html><body><table><tbody>
<tr><th id="kinoko" rowspan="2">キノコ</th><td>カエンタケ</td>
    <td><a href="https://www.mhlw.go.jp/x/summary.html">概要版</a></td></tr>
<tr><td>カキシメジ</td>
    <td><a href="https://www.mhlw.go.jp/x/short.html">概要版</a>、
        <a href="https://www.mhlw.go.jp/x/detail.html">詳細版</a></td></tr>
</tbody></table>
<table><tbody><tr><th id="higher" rowspan="1">高等植物</th><td>アジサイ</td>
    <td><a href="https://www.mhlw.go.jp/x/plant.html">概要版</a></td></tr></tbody></table>
</body></html>"""


class ParseIndex(unittest.TestCase):
    def test_rows_detail_preferred_and_th_row_kept(self):
        rows = mhlw.parse_index(INDEX)
        self.assertEqual(rows, [
            ("カエンタケ", "https://www.mhlw.go.jp/x/summary.html"),
            ("カキシメジ", "https://www.mhlw.go.jp/x/detail.html")])   # 詳細版 wins
        self.assertNotIn("アジサイ", [n for n, _ in rows])              # plants excluded


class ParseTitle(unittest.TestCase):
    def test_mixed_parens_and_authors(self):
        # the real title shape: full-width open, half-width close, authors inside
        ja, latin = mhlw.parse_title(
            "自然毒のリスクプロファイル：カキシメジ（Tricholoma ustale Fr.)　キシメジ科キシメジ属｜厚生労働省")
        self.assertEqual(ja, "カキシメジ")
        self.assertEqual(latin, "Tricholoma ustale")     # first two Latin words only

    def test_no_latin(self):
        self.assertEqual(mhlw.parse_title("自然毒のリスクプロファイル：何か｜厚生労働省"), (None, None))
        self.assertEqual(mhlw.parse_title(""), (None, None))
        self.assertEqual(mhlw.parse_title("自然毒のリスクプロファイル：ドウシンタケ（Amanita sp.)｜x"),
                         ("ドウシンタケ", None))          # one word is not a binomial


class TermsText(unittest.TestCase):
    def test_body_extracted_after_col1_marker(self):
        page = "<div>nav</div><div class='m-grid__col1'>リンクフリーです。<br>出典を記載してください。</div>"
        self.assertIn("リンクフリーです。", mhlw.terms_text(page))


class MatchEntry(unittest.TestCase):
    def setUp(self):
        self.con = duckdb.connect()
        write_tsv_gz("x.tsv.gz", [
            ("8001", "SPECIES", "Ficta prima", "Fungi", "C1", "accepted", "Ficta prima",
             "", "", "Fictaceae", "Ficta"),
            ("8002", "SPECIES", "Ficta altissima", "Fungi", "C2", "synonym", "Ficta altissima",
             "C1", "Ficta prima", "Fictaceae", "Ficta")])
        from pipeline.col_xr import load_map
        load_map(self.con, "x.tsv.gz")
        os.remove("x.tsv.gz")
        self.public = mhlw.PublicIndex([
            {"sp_key": "C1", "col_key": "C1", "name": "Ficta prima", "genus": "Ficta",
             "family": "Fictaceae", "legacy_keys": ["8001"]},
            {"sp_key": "C9", "col_key": "C9", "name": "Ficta publica", "genus": "Ficta",
             "family": "Fictaceae", "legacy_keys": ["8009"]}])

    def tearDown(self):
        self.con.close()

    def test_exact_public_name(self):
        self.assertEqual(mhlw.match_entry("Ficta prima", "x", self.public, self.con),
                         ("C1", "exact public name"))

    def test_crosswalk_synonym_to_public_col_key(self):
        # a synonym usage of C1 (not itself a public name) resolves through colmap
        self.assertEqual(mhlw.match_entry("Ficta altissima", "x", self.public, self.con),
                         ("C1", "crosswalk to COL key"))

    def test_crosswalk_miss_never_lives(self):
        self.assertEqual(mhlw.match_entry("Nihil absurdum", "x", self.public, self.con,
                                          live=lambda n: None),
                         (None, "no public species matched"))

    def test_live_fallback_public(self):
        # not a public name, not in the crosswalk — the live matcher's backbone
        # key maps through the crosswalk onto a public COL id
        hit = {"speciesKey": "8001", "matchType": "FUZZY"}
        self.assertEqual(mhlw.match_entry("Ficta vulgaris", "x", self.public, self.con,
                                          live=lambda n: hit), ("C1", "live match -> crosswalk"))

    def test_live_legacy_key_public(self):
        unmapped = mhlw.PublicIndex([
            {"sp_key": "L8009", "col_key": None, "name": "Ficta sylvatica", "genus": "Ficta",
             "family": "Fictaceae", "legacy_keys": ["8009"]}])
        hit = {"speciesKey": "8009", "matchType": "EXACT"}
        self.assertEqual(mhlw.match_entry("Ficta sylvatica alba", "x", unmapped, self.con,
                                          live=lambda n: hit),
                         ("L8009", "live match (legacy key)"))

    def test_masked_never_surfaces(self):
        # a live hit whose COL id is NOT public returns nothing, and says only
        # that no public species matched (never that a masked one did)
        masked = {"speciesKey": "8001", "matchType": "EXACT"}
        public_without_c1 = mhlw.PublicIndex([
            {"sp_key": "C9", "col_key": "C9", "name": "Ficta publica", "genus": "Ficta",
             "family": "Fictaceae", "legacy_keys": ["8009"]}])
        self.assertEqual(mhlw.match_entry("Ficta prima", "x", public_without_c1, self.con,
                                          live=lambda n: masked),
                         (None, "no public species matched"))


if __name__ == "__main__":
    unittest.main()
