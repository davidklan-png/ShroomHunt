"""TASK-003: reference geography build — clip, simplify, determinism, MapReference shape.

Synthetic tiny GeoJSON under tests/fixtures/naturalearth/ stands in for the
pinned 10 MB Natural Earth download: no network, no real coastline.
"""
import json, os, sys, unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from pipeline.build_map_reference import (AnchorLostError, build, point_in_polygon,
                                          read_places, ring_bbox)

FIX = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fixtures", "naturalearth")


def build_fixture(**kw):
    with open(os.path.join(FIX, "tiny_land.geojson"), encoding="utf-8") as f:
        geo = json.load(f)
    kw.setdefault("url", "https://example.test/ne_10m_land.geojson")
    return geo, build(geo, read_places(os.path.join(FIX, "map_places.csv")), **kw)


def honto(ref):
    """The fixture's Okinawa-Hontō stand-in: the only polygon with a hole."""
    return next(p for p in ref["land"] if len(p) == 2)


class Clip(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.geo, cls.ref = build_fixture()

    def test_keeps_inside_drops_outside_cuts_at_border(self):
        # Hontō + hole, the border-straddling islet and Ishigaki survive; the islet past 155°E is gone
        self.assertEqual(len(self.ref["land"]), 3)
        max_lon = max(p[0] for ring in [r for poly in self.ref["land"] for r in poly] for p in ring)
        self.assertEqual(max_lon, 155.0)  # straddler is cut exactly at the east border
        for poly in self.ref["land"]:
            for ring in poly:
                for lon, lat in ring:
                    self.assertTrue(121 <= lon <= 155 and 19 <= lat <= 47, (lon, lat))

    def test_min_size_drops_small_islets_keeps_anchor_land(self):
        # at 0.5° the Ishigaki stand-in (0.3° span) and Hontō's hole (0.1°) are dropped;
        # Hontō itself and the 1°-span straddler stay, anchors intact
        _, ref = build_fixture(min_size=0.5)
        self.assertEqual(len(ref["land"]), 2)
        self.assertTrue(any(point_in_polygon((127.68, 26.21), p) for p in ref["land"]))


class Simplify(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.geo, cls.ref = build_fixture()

    def test_tolerance_reduces_vertices_while_keeping_anchors(self):
        outer = honto(self.ref)[0]
        self.assertLess(len(outer), 16)   # fixture outer ring has 16 closed points
        self.assertGreaterEqual(len(outer), 4)
        for lon, lat in ((127.68, 26.21), (128.2, 26.7)):  # Naha, Yanbaru
            self.assertTrue(point_in_polygon((lon, lat), honto(self.ref)))

    def test_over_simplified_land_raises_instead_of_shipping(self):
        # a 5° tolerance collapses every ring below the minimum bbox size
        with self.assertRaises(AnchorLostError):
            build_fixture(tolerance=5.0)


class Rings(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.geo, cls.ref = build_fixture()

    def test_closed_rings_with_holes_after_outer(self):
        for poly in self.ref["land"]:
            for ring in poly:
                self.assertEqual(ring[0], ring[-1])
        outer, hole = honto(self.ref)[0], honto(self.ref)[1]
        os_, ow, on_, oe = ring_bbox(outer)
        hs, hw, hn, he = ring_bbox(hole)
        self.assertTrue(os_ <= hs and ow <= hw and hn <= on_ and he <= oe)  # hole inside outer


class OutputShape(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.geo, cls.ref = build_fixture()

    def test_mapreference_shape(self):
        self.assertEqual(set(self.ref), {"schema_version", "source", "bbox", "land", "places"})
        self.assertEqual(self.ref["schema_version"], 1)
        self.assertEqual(self.ref["source"], {"name": "Natural Earth", "release": "5.1.1",
                                              "url": "https://example.test/ne_10m_land.geojson"})
        self.assertEqual(self.ref["bbox"], [19, 47, 121, 155])  # [20,46] × [122,154] + 1° margin

    def test_fixed_decimals_and_sorted_places(self):
        for poly in self.ref["land"]:
            for ring in poly:
                for lon, lat in ring:
                    self.assertEqual((round(lon, 3), round(lat, 3)), (lon, lat))
        ens = [p["en"] for p in self.ref["places"]]
        self.assertEqual(ens, sorted(ens))

    def test_deterministic_bytes(self):
        _, again = build_fixture()
        dump = lambda r: json.dumps(r, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
        self.assertEqual(dump(self.ref), dump(again))


class Places(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.geo, cls.ref = build_fixture()

    def test_csv_round_trip(self):
        by_en = {p["en"]: p for p in self.ref["places"]}
        self.assertEqual(by_en["Naha"]["ja"], "那覇")
        self.assertEqual(by_en["Naha"]["at"], [127.682, 26.212])  # rounded, lon first
        self.assertEqual(by_en["Naha"]["area_id"], "okinawa-honto")
        self.assertEqual(by_en["Nowhere"]["aliases"], ["Nowhere", "far away", "nowhere"])  # sorted, "||" dropped
        self.assertNotIn("area_id", by_en["Nowhere"])  # blank area_id omits the key


class AnchorGuard(unittest.TestCase):
    def test_missing_anchor_land_raises(self):
        with open(os.path.join(FIX, "tiny_land.geojson"), encoding="utf-8") as f:
            geo = json.load(f)
        geo["features"][0]["geometry"]["coordinates"] = geo["features"][0]["geometry"]["coordinates"][1:]
        with self.assertRaises(AnchorLostError):
            build(geo, read_places(os.path.join(FIX, "map_places.csv")))


if __name__ == "__main__":
    unittest.main()
