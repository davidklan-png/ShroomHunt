"""TASK-022 item 5: the MushroomExpert parsers — same-host link extraction with
text folding, the meta-refresh redirect hop, and public-taxon matching by link
text (binomial for species, bare token for genus). Fictional taxa; no network.
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from pipeline.sources import mushroomexpert as me

SPECIES = [
    {"sp_key": "C1", "col_key": "C1", "name": "Ficta prima", "genus": "Ficta",
     "family": "F", "legacy_keys": ["1"]},
    {"sp_key": "C2", "col_key": "C2", "name": "Ficta altera maxima", "genus": "Ficta",
     "family": "F", "legacy_keys": ["2"]},
    {"sp_key": "C3", "col_key": None, "name": "Altera secunda", "genus": "Altera",
     "family": "F", "legacy_keys": ["3"]},
]

PAGE = """<HTML><BODY>
<A HREF="ficta_prima.html"><i>Ficta prima</i></A>
<A HREF="ficta.html">Ficta</A>
<A HREF='altera_secunda.html'>Alter&aacute; secunda</A>
<A HREF="http://elsewhere.com/x.html">Ficta prima</A>
<A HREF="glossary.html#gills">gills</A>
<A HREF="http://www.mushroomexpert.com/boletus.html">Boletus</A>
</BODY></HTML>"""


class PageLinks(unittest.TestCase):
    def test_same_host_links_with_folded_text(self):
        links = me.page_links(PAGE, me.INDEX)
        urls = [u for u, _ in links]
        self.assertIn("https://www.mushroomexpert.com/ficta_prima.html", urls)
        self.assertNotIn("http://elsewhere.com/x.html", urls)     # other host
        texts = {u: t for u, t in links}
        self.assertEqual(texts["https://www.mushroomexpert.com/ficta.html"], "Ficta")
        self.assertEqual(texts.get("https://www.mushroomexpert.com/altera_secunda.html"),
                         "Alterá secunda")                         # entity-decoded


class MetaRefresh(unittest.TestCase):
    def test_hop_is_followed(self):
        page = ('<HTML><HEAD><TITLE>Redirecting...</TITLE>'
                '<meta HTTP-EQUIV="Refresh" CONTENT="0;URL=gilled.html">'
                "</HEAD><BODY></BODY></HTML>")
        self.assertEqual(me.meta_refresh(page, "https://www.mushroomexpert.com/agaricales.html"),
                         "https://www.mushroomexpert.com/gilled.html")

    def test_plain_page_has_no_hop(self):
        self.assertIsNone(me.meta_refresh(PAGE, me.INDEX))


class ExtractLinks(unittest.TestCase):
    def test_species_and_genus_matching(self):
        sp, gen = me.extract_links(PAGE, SPECIES)
        self.assertEqual(sp, {"C1": "https://www.mushroomexpert.com/ficta_prima.html",
                              "C3": "https://www.mushroomexpert.com/altera_secunda.html"})
        self.assertEqual(gen, {"ficta": "https://www.mushroomexpert.com/ficta.html"})
        # a trinomial name still matches on its first two words
        self.assertNotIn("C2", sp)


if __name__ == "__main__":
    unittest.main()
