"""TASK-022 item 4: the Mycoscience matcher — binomial bigram matching (epithet
homonyms harmless), genus-only hits, JATS-tag stripping in abstracts. Fictional
taxa and titles; no network.
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from pipeline.sources import mycoscience as ms

BINOMIALS = {"ficta prima": "C1", "ficta altera": "C2", "altera maxima": "C3"}
GENERA = {"ficta", "boletus"}


class Match(unittest.TestCase):
    def test_binomial_in_title(self):
        sp, gen = ms.matches("Notes on <i>Ficta prima</i> from Okinawa", BINOMIALS, GENERA)
        self.assertEqual(sp, {"ficta prima"})
        self.assertEqual(gen, {"ficta"})

    def test_epithet_homonym_is_not_a_binomial_hit(self):
        # 'prima' alone (another genus's epithet, or plain Latin) must not match
        sp, _ = ms.matches("Prima observations on Boletus edulis", BINOMIALS, GENERA)
        self.assertEqual(sp, set())

    def test_bigram_needs_adjacency(self):
        sp, _ = ms.matches("Ficta from Okinawa and Prima from Kyoto", BINOMIALS, GENERA)
        self.assertEqual(sp, set())

    def test_genus_only_hit(self):
        sp, gen = ms.matches("A new Boletus from Honshu", BINOMIALS, GENERA)
        self.assertEqual(sp, set())
        self.assertEqual(gen, {"boletus"})

    def test_abstract_with_jats_tags(self):
        item = {"title": ["Ficta altera sp. nov."],
                "abstract": "<p>Abstract: <italic>Ficta altera</italic> differs from "
                            "<italic>Ficta prima</italic>.</p>"}
        sp, gen = ms.matches(ms.article_text(item), BINOMIALS, GENERA)
        self.assertEqual(sp, {"ficta altera", "ficta prima"})
        self.assertEqual(gen, {"ficta"})

    def test_fold_diacritics(self):
        # macrons decompose under NFKD and the combining mark is dropped
        sp, _ = ms.matches("Fīcta prīma revisited", BINOMIALS, GENERA)
        self.assertEqual(sp, {"ficta prima"})


class ParseJstage(unittest.TestCase):
    ATOM = """<feed xmlns="http://www.w3.org/2005/Atom"><result><status>0</status></result>
<opensearch:totalResults>2</opensearch:totalResults>
<entry><article_title>
  <en><![CDATA[Two rare species of <i>Ficta</i> collected in Fukuoka]]></en>
  <ja><![CDATA[福岡県で採集された<Ficta>属の稀菌2種]]></ja>
</article_title>
<article_link><en>https://www.jstage.jst.go.jp/article/x/1/2/_article</en></article_link>
</entry>
<entry><article_title><ja><![CDATA[和文のみ]]></ja></article_title>
<article_link><en>https://www.jstage.jst.go.jp/article/y/_article</en></article_link>
</entry>
</feed>"""

    def test_entries_parsed_and_deduped_shape(self):
        arts = ms.parse_jstage(self.ATOM)
        self.assertEqual(len(arts), 2)
        self.assertEqual(arts[0]["title"], "Two rare species of Ficta collected in Fukuoka")
        self.assertTrue(arts[0]["url"].startswith("https://www.jstage.jst.go.jp/"))
        self.assertEqual(arts[1]["title"], "和文のみ")            # ja fallback

    def test_empty_and_garbage(self):
        self.assertEqual(ms.parse_jstage(""), [])
        self.assertEqual(ms.parse_jstage("<html>not atom</html>"), [])


if __name__ == "__main__":
    unittest.main()
