"""TASK-022 item 1: the Wikidata coverage parsers — SPARQL result shape,
P18 FilePath-URL → file title, and the binding CC0/CC BY/CC BY-SA licence
filter (spec §2). Fictional taxa and files only; no network.
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from pipeline.sources import wikidata as wd


def sparql(bindings):
    return {"results": {"bindings": bindings}}


B = lambda k, **kw: {"key": {"value": k}, **{f: {"value": v} for f, v in kw.items()}}


class ParseSparql(unittest.TestCase):
    def test_item_sitelinks_and_file_title(self):
        doc = sparql([
            B("1001", item="http://www.wikidata.org/entity/Q42",
              jawp="Tricholoma_%E5%B1%9E", enwp="Tricholoma_fictum",
              p18="http://commons.wikimedia.org/wiki/Special:FilePath/Tricholoma%20fictum.jpg"),
            B("1002", item="http://www.wikidata.org/entity/Q43"),
        ])
        rows = wd.parse_sparql(doc)
        self.assertEqual(rows["1001"]["qid"], "Q42")
        self.assertEqual(rows["1001"]["jawp"], "Tricholoma_属")
        self.assertEqual(rows["1001"]["enwp"], "Tricholoma_fictum")
        self.assertEqual(rows["1001"]["p18"], ["Tricholoma fictum.jpg"])
        self.assertEqual(rows["1002"]["p18"], [])
        self.assertNotIn("1003", rows)

    def test_duplicate_keys_merge_first_values(self):
        doc = sparql([B("1001", item="http://www.wikidata.org/entity/Q1"),
                      B("1001", item="http://www.wikidata.org/entity/Q2",
                        p18="http://commons.wikimedia.org/wiki/Special:FilePath/A.jpg")])
        rows = wd.parse_sparql(doc)
        self.assertEqual(rows["1001"]["qid"], "Q1")      # first binding wins
        self.assertEqual(rows["1001"]["p18"], ["A.jpg"])   # absent fields fill in


class Licence(unittest.TestCase):
    def test_acceptable(self):
        ok = [("CC BY-SA 4.0", "https://creativecommons.org/licenses/by-sa/4.0"),
              ("CC BY 2.0", "https://creativecommons.org/licenses/by/2.0"),
              ("CC0", "https://creativecommons.org/publicdomain/zero/1.0"),
              ("cc by-sa 3.0", ""), ("Attribution", "https://creativecommons.org/licenses/by/4.0")]
        for short, url in ok:
            self.assertIn(wd.classify_licence(short, url), wd.ACCEPTABLE, (short, url))

    def test_unacceptable(self):
        bad = [("CC BY-NC 4.0", ""), ("CC BY-NC-SA 3.0", ""), ("CC BY-ND 3.0", ""),
               ("Public domain", ""), ("GFDL", ""), ("Fair use", ""), ("", ""),
               ("Creative Commons Attribution-NonCommercial", ""),
               ("cc0-ish nc", "")]                        # the NC veto beats a CC0 mention
        for short, url in bad:
            self.assertIsNone(wd.classify_licence(short, url), (short, url))


class ParseImageinfo(unittest.TestCase):
    def test_extmetadata_licence(self):
        doc = {"query": {"pages": {"1": {"title": "File:A.jpg", "imageinfo": [
            {"extmetadata": {"LicenseShortName": {"value": "CC BY-SA 4.0"},
                             "LicenseUrl": {"value": "https://x"}}}]}}}}
        self.assertEqual(wd.parse_imageinfo(doc), {"File:A.jpg": "CC BY-SA"})

    def test_missing_page_or_metadata_is_none(self):
        doc = {"query": {"pages": {"-1": {"title": "File:Missing.jpg", "missing": ""}}}}
        self.assertEqual(wd.parse_imageinfo(doc), {"File:Missing.jpg": None})
        doc2 = {"query": {"pages": {"1": {"title": "File:B.jpg", "imageinfo": [
            {"extmetadata": {}}]}}}}
        self.assertEqual(wd.parse_imageinfo(doc2), {"File:B.jpg": None})


if __name__ == "__main__":
    unittest.main()
