"""TASK-022 item 3: the Hosoya-2016 parser — structural row recovery from the
garbled pdftotext layout (split/flipped/dropped No digits, wrapped locality
cells, CJK stand-ins inside Latin names), the fail-closed sanity checks, and
the fuzzy matcher's longest-match preference. Fictional taxa; no network.
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import duckdb

from pipeline.sources import hosoya
from test_col_xr import write_tsv_gz

# A miniature of the real 表3 layout: heading + header, then CONSECUTIVE rows
# 1..14 exercising every digit quirk — plain, split across lines (rows 10, 11),
# flipped fragments ("2\n1 20" for No 12), a dropped No digit ("4 107" for
# No 14) — plus a wrapped distribution (日本（京都・ / 滋賀）), CJK inside the
# Latin name, a synonym 和名 line, a page footer, and the *ID footnote.
TEXT = """表 3 本調査の結果による日本の固有種きのこリスト
No ID*               現在名                        和名               分布（保育社図鑑）
 1   4 Ficta prima Hongo                  フィクタケ            日本（滋賀）
 2   6 Ficta secunda Hongo                セクンダタケ          日本
 3   9 Fictaτ tertia Hongo                テルティアタケ        日本（京都・
                                                                        滋賀）
 4  11 Ficta quarta Hongo                 クアルタタケ          日本
 5  12 Ficta quinta Hongo                 クインタタケ          日本
 6  13 Ficta sexta Hongo                  セクスタタケ          日本
 7  14 Ficta septima Hongo                セプティマタケ        日本
 8  15 Ficta octava Hongo                 オクタバタケ          日本
 9  16 Ficta nona Hongo                   ノナタケ              日本
1
0   17 Ficta decima Hongo                 デシマタケ            日本
1
1   18 Ficta undecima Hongo               ウンデシマタケ        日本
2
1   20 Ficta duodecima Hongo              ドゥオデシマタケ      日本
13   21 Ficta tertiadecima Hongo          シンゾウタケ          日本
                                                               シノニムタケ
                                       ‑31ー
1 107 Ficta maxima Yasuda et Kudo         マキシマタケ          日本
0句 n
*IDは表 S1,S2の作業リスト学名の ID番号に対応
"""

ROWS = None


def parsed():
    region = hosoya.table_region(TEXT)
    return hosoya.parse_rows(region)


class TableRegion(unittest.TestCase):
    def test_rows_and_pages(self):
        rows, anomalies, anchors = parsed()
        self.assertEqual([r["no"] for r in rows], list(range(1, 15)))
        self.assertEqual(anchors - 1, len(rows))          # heading carries one 日本
        self.assertEqual([r["page"] for r in rows],
                         [31] * 13 + [32])                # the ‑31ー footer ends p.31
        self.assertEqual(anomalies, ["No 14 (digit dropped: 107)"])

    def test_latin_recovery(self):
        rows, _, _ = parsed()
        self.assertEqual(hosoya.normalize(rows[0]["latin"]), "fictaprimahongo")
        self.assertEqual(hosoya.normalize(rows[2]["latin"]), "fictatertiahongo")  # τ dropped
        # the trailing synonym 和名 line is stripped from row 13's latin
        self.assertNotIn("シノニム", rows[12]["latin"])
        self.assertEqual(rows[13]["name_ja"], "マキシマタケ")
        # the "0句 n" fragment glued to digits is not a row
        self.assertTrue(all(r["no"] != 0 for r in rows))

    def test_sanity_fails_closed(self):
        rows, anomalies, anchors = parsed()
        with self.assertRaises(SystemExit):
            hosoya.sanity_check(rows, anomalies, anchors + 1)     # a row was lost
        with self.assertRaises(SystemExit):
            hosoya.sanity_check(rows[:2], anomalies, anchors)     # far too short
        with self.assertRaises(SystemExit):
            hosoya.sanity_check(rows, ["a"] * 6, anchors)         # numbering misread

    def test_missing_heading_refuses(self):
        with self.assertRaises(SystemExit):
            hosoya.table_region("no table here")


class Normalize(unittest.TestCase):
    def test_letters_only(self):
        self.assertEqual(hosoya.normalize("Am αni\ntα，αlbo"), "amnitlbo")
        self.assertEqual(hosoya.normalize(""), "")

    def test_strip_authors(self):
        self.assertEqual(hosoya.strip_authors("fictaprimahongo"), "fictaprima")
        self.assertEqual(hosoya.strip_authors("fictamaximayasuda"), "fictamaxima")
        self.assertEqual(hosoya.strip_authors("fictamaximaetkudo"), "fictamaximaetkudo")
        self.assertEqual(hosoya.strip_authors("ficta"), "ficta")   # never eats the name


class MatchSpeciesRows(unittest.TestCase):
    def setUp(self):
        self.con = duckdb.connect()
        write_tsv_gz("h.tsv.gz", [
            ("8001", "SPECIES", "Ficta prima", "Fungi", "C1", "accepted", "Ficta prima",
             "", "", "Fictaceae", "Ficta"),
            ("8003", "SPECIES", "Ficta pseudotertia", "Fungi", "C3", "accepted",
             "Ficta pseudotertia", "", "", "Fictaceae", "Ficta"),
            ("8004", "SPECIES", "Ficta tertia", "Fungi", "C4", "accepted", "Ficta tertia",
             "", "", "Fictaceae", "Ficta")])
        from pipeline.col_xr import load_map
        load_map(self.con, "h.tsv.gz")
        os.remove("h.tsv.gz")
        self.public = [
            {"sp_key": "C1", "col_key": "C1", "name": "Ficta prima", "genus": "Ficta",
             "family": "Fictaceae", "legacy_keys": ["8001"]},
            {"sp_key": "C3", "col_key": "C3", "name": "Ficta pseudotertia", "genus": "Ficta",
             "family": "Fictaceae", "legacy_keys": ["8003"]},
            {"sp_key": "C8", "col_key": "C8", "name": "Ficta nonpublic", "genus": "Ficta",
             "family": "Fictaceae", "legacy_keys": ["8008"]}]

    def tearDown(self):
        self.con.close()

    def rows(self, latin):
        return [{"no": 1, "id": "", "latin": latin, "name_ja": "", "page": 81}]

    def test_exact_public_name(self):
        out = hosoya.match_species_rows(self.rows("Ficta prima Hongo"), self.public,
                                        self.con, live=lambda n: None)
        self.assertEqual((out[0][1], out[0][3]), ("C1", "exact public name"))

    def test_fuzzy_prefers_the_full_binomial(self):
        # the garbled text lost the 'p' (a stand-in glyph): '…seudotertia…' still
        # must resolve to Ficta pseudotertia, not to the perfect substring
        # Ficta tertia
        out = hosoya.match_species_rows(self.rows("Ficta seudotertia Hongo"), self.public,
                                        self.con, live=lambda n: None)
        self.assertEqual(out[0][1], "C3")

    def test_crosswalk_hit_outside_public_data_is_not_a_row(self):
        out = hosoya.match_species_rows(self.rows("Ficta tertia Hongo"), self.public,
                                        self.con, live=lambda n: None)
        self.assertIsNone(out[0][1])
        self.assertIn("not a public species here", out[0][3])


if __name__ == "__main__":
    unittest.main()
