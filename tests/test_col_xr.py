"""TASK-018: COL XR re-keying — load_map/apply over a tiny crosswalk fixture,
species identity merging under one COL id, and the gate's col_key masking.

The crosswalk fixture uses the real gbif-col265.tsv.gz column names and the
fictional taxa of tests/fixtures/reference (test_public_taxa.py); nothing reads
data/raw/. The col_mask_map fixture is the generated-shape file the gate loads
when a --col-map-keyed build is in progress.
"""
import csv, gzip, json, os, sys, tempfile, unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import duckdb

from pipeline import col_xr
from pipeline.public_taxa import PublicTaxaError
from test_public_taxa import FIX, gate_with, write_coverage
from test_public_taxa_outputs import JP_COLUMNS, run_builder

# (gbif:ID, gbif:rank, gbif:scientificName, gbif:kingdom, col:ID, col:status,
#  col:scientificName, col:acceptedID, col:acceptedScientificName, col:family, col:genus)
XWALK = [
    # accepted: 9001 Rareus japonicus; 9002 Dubius ambiguus (Red List fixture keys)
    ("9001", "SPECIES", "Rareus japonicus", "Fungi", "CRL1", "accepted", "Rareus japonicus", "", "", "Rareaceae", "Rareus"),
    ("9002", "SPECIES", "Dubius ambiguus", "Fungi", "CRL2", "accepted", "Dubius ambiguus", "", "", "Dubiaceae", "Dubius"),
    # the commercial mask names + aliases resolve to two accepted taxa
    ("7001", "SPECIES", "Matsutakus fictus", "Fungi", "CRL3", "accepted", "Matsutakus fictus", "", "", "Fictaceae", "Matsutakus"),
    ("7002", "SPECIES", "Tricholoma fictum", "Fungi", "CRL9", "synonym", "Tricholoma fictum", "CRL3", "Matsutakus fictus", "Fictaceae", "Matsutakus"),
    ("7003", "SPECIES", "Armillaria ficta", "Fungi", "CRL10", "synonym", "Armillaria ficta", "CRL3", "Matsutakus fictus", "Fictaceae", "Matsutakus"),
    ("7004", "SPECIES", "Maskedus apocryphus", "Fungi", "CRL4", "accepted", "Maskedus apocryphus", "", "", "Fictaceae", "Maskedus"),
    ("7011", "SPECIES", "Maskedus apocryphus Quél.", "Fungi", "CRL11", "synonym", "Maskedus apocryphus Quél.", "CRL4", "Maskedus apocryphus", "Fictaceae", "Maskedus"),
    ("7005", "SPECIES", "Oldus apocryphus", "Fungi", "CRL12", "synonym", "Oldus apocryphus", "CRL4", "Maskedus apocryphus", "Fictaceae", "Maskedus"),
    ("7006", "SPECIES", "Falsus apocryphus", "Fungi", "CRL13", "synonym", "Falsus apocryphus", "CRL4", "Maskedus apocryphus", "Fictaceae", "Maskedus"),
    ("7007", "SPECIES", "Boletus fictus", "Fungi", "CRL14", "synonym", "Boletus fictus", "CRL3", "Matsutakus fictus", "Fictaceae", "Matsutakus"),
    # two backbone species lumped under one COL id (counts/lists merge to one)
    ("7012", "SPECIES", "Candidus merus", "Fungi", "CRL7", "accepted", "Candidus merus", "", "", "Candidaceae", "Candidus"),
    ("7013", "SPECIES", "Candidus verus", "Fungi", "CRL8", "synonym", "Candidus verus", "CRL7", "Candidus merus", "Candidaceae", "Candidus"),
    ("7014", "SPECIES", "Duo prima", "Fungi", "CRL14", "accepted", "Duo prima", "", "", "Duaceae", "Duo"),
    ("7015", "SPECIES", "Duo secunda", "Fungi", "CRL15", "synonym", "Duo secunda", "CRL14", "Duo prima", "Duaceae", "Duo"),
    # 9003 is exception-ignored (never gate-loaded); a non-Fungi row is filtered out
    ("9003", "SPECIES", "Publicus vulgaris", "Fungi", "CRL5", "accepted", "Publicus vulgaris", "", "", "Fictaceae", "Publicus"),
    ("888", "SPECIES", "Plantus vulgaris", "Plantae", "CRL6", "accepted", "Plantus vulgaris", "", "", "Plantaceae", "Plantus"),
]
XWALK_HEADER = ["gbif:ID", "gbif:rank", "gbif:scientificName", "gbif:kingdom", "gbif:phylum",
                "gbif:class", "gbif:order", "gbif:family", "gbif:genus", "col:ID", "col:status",
                "col:scientificName", "col:acceptedID", "col:acceptedScientificName",
                "col:kingdom", "col:phylum", "col:class", "col:order", "col:family", "col:genus"]


def write_tsv_gz(path, rows, drop=()):
    """A gzipped crosswalk fixture with the real gbif-col265 column names; `drop`
    removes rows by gbif:ID (unmatched-name tests). Row tuple: (gbif:ID, gbif:rank,
    gbif:scientificName, gbif:kingdom, col:ID, col:status, col:scientificName,
    col:acceptedID, col:acceptedScientificName, col:family, col:genus)."""
    keep = [r for r in rows if r[0] not in drop]
    with gzip.open(path, "wt", encoding="utf-8", newline="") as f:
        w = csv.writer(f, delimiter="\t", lineterminator="\n")
        w.writerow(XWALK_HEADER)
        for gbif_id, rank, name, kingdom, col_id, status, col_name, acc_id, acc_name, fam, gen in keep:
            genus = name.split()[0]                     # backbone classification (unused beyond the load)
            w.writerow([gbif_id, rank, name, kingdom, "Basidiomycota", "Agaricomycetes", "Agaricales",
                        fam, genus, col_id, status, col_name, acc_id, acc_name,
                        "Fungi", "Basidiomycota", "Agaricomycetes", "Agaricales", fam, gen])
    return path


class LoadAndApply(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.con = duckdb.connect()
        self.tsv = write_tsv_gz(os.path.join(self.tmp, "col.tsv.gz"), XWALK)

    def tearDown(self):
        self.con.close()

    def test_load_map_keeps_fungi_rows_and_resolves_accepted_identity(self):
        col_xr.load_map(self.con, self.tsv)
        rows = self.con.execute('select gbif_id, col_id, col_accepted_id, col_accepted_name '
                                "from colmap where gbif_id in ('7002','9001','888') order by 1").fetchall()
        self.assertEqual(rows, [("7002", "CRL9", "CRL3", "Matsutakus fictus"),   # synonym resolves
                                ("9001", "CRL1", "CRL1", "Rareus japonicus")])   # accepted falls back; Plantae gone

    def test_apply_keys_records_by_their_accepted_col_id(self):
        self.con.execute(f"create table jp(speciesKey varchar, species varchar, family varchar)")
        self.con.executemany("insert into jp values (?,?,?)",
                             [("7001", "Matsutakus fictus", "Fictaceae"),
                              ("7002", "Tricholoma fictum", "Tricholomataceae"),
                              ("9999", "Neminem novi", "Incertae"),
                              (None, None, "Fictaceae")])
        col_xr.load_map(self.con, self.tsv)
        col_xr.apply(self.con)
        rows = self.con.execute("select speciesKey, col_key, col_name, col_family from jp order by 1 nulls last").fetchall()
        self.assertEqual(rows, [("7001", "CRL3", "Matsutakus fictus", "Fictaceae"),
                                ("7002", "CRL3", "Matsutakus fictus", "Fictaceae"),  # backbone synonyms merge
                                ("9999", None, None, None),                          # unmapped stays null
                                (None, None, None, None)])

    def test_exprs_fall_back_to_backbone_columns(self):
        legacy = {"speciesKey", "species", "family"}
        keyed = legacy | {"col_key", "col_name", "col_family"}
        e = col_xr.exprs("jp", legacy)
        self.assertEqual(e["sp_key"], "coalesce(null, 'L' || jp.speciesKey::varchar)")
        self.assertEqual(e["family"], "coalesce(null, jp.family)")
        self.assertIn("jp.col_family", col_xr.exprs("jp", keyed)["family"])


class MergedSpecies(unittest.TestCase):
    """Two legacy keys that COL lumps into one accepted id count and list once,
    in the dashboard totals and the area species table; the public rows carry
    the COL key field and the COL accepted name."""

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp()
        cls.refs = ["--mask", os.path.join(FIX, "taxon_mask.csv"),
                    "--coverage", write_coverage(cls.tmp),
                    "--exceptions", os.path.join(FIX, "match_exceptions.csv"),
                    "--redlist", os.path.join(FIX, "redlist.json")]
        cls.col_mask = os.path.join(FIX, "col_mask_map.csv")
        tsv = write_tsv_gz(os.path.join(cls.tmp, "col.tsv.gz"), XWALK)
        cls.db = os.path.join(cls.tmp, "merged.duckdb")
        c = duckdb.connect(cls.db)
        c.execute(f"create table jp({JP_COLUMNS})")
        c.executemany("insert into jp values (" + ",".join(["?"] * 15) + ")", [
            # 7012/7013: two PUBLIC backbone species, lumped by COL into CRL7; the
            # backbone families differ to prove grouping follows the COL family
            ("7012", "Candidus merus", "Candidus", "Candidaceae", "SPECIES", "HUMAN_OBSERVATION",
             "Agaricomycetes", "Basidiomycota", "26.1", "127.7", "3", "2019", None, "Okinawa", "dx"),
            ("7013", "Candidus verus", "Candidus", "Otheraceae", "SPECIES", "PRESERVED_SPECIMEN",
             "Agaricomycetes", "Basidiomycota", "26.2", "127.7", "5", "2021", None, "Okinawa", "dx"),
            ("9999", "Neminem novi", "Neminem", "Incertae", "SPECIES", "HUMAN_OBSERVATION",
             "Agaricomycetes", "Basidiomycota", "26.3", "127.7", "7", "2020", None, "Okinawa", "dx"),
        ])
        c.execute('create table nb(species varchar, phylum varchar, "order" varchar, family varchar, '
                  "genus varchar, class varchar, numberOfOccurrences varchar, iucnRedListCategory varchar, "
                  "taxonRank varchar, taxonomicStatus varchar, speciesKey varchar)")
        c.executemany("insert into nb values (" + ",".join(["?"] * 11) + ")", [
            ("Duo prima", "Basidiomycota", "Agaricales", "Duaceae", "Duo", "Agaricomycetes",
             "30", None, "SPECIES", "ACCEPTED", "7014"),
            ("Duo secunda", "Basidiomycota", "Agaricales", "Otheraceae", "Duo", "Agaricomycetes",
             "20", None, "SPECIES", "ACCEPTED", "7015"),
        ])
        c.execute("create table mo(speciesKey varchar, countryCode varchar)")
        c.executemany("insert into mo values (?, ?)", [("7014", "JP"), ("7015", "JP")])
        col_xr.load_map(c, tsv)
        col_xr.apply(c)
        c.close()
        cls.dash = os.path.join(cls.tmp, "dashboard.json")
        r = run_builder("build_dashboard_data.py", "--db", cls.db, "--out", cls.dash,
                        *cls.refs, "--col-mask-map", cls.col_mask)
        assert r.returncode == 0, r.stderr
        with open(cls.dash) as f:
            cls.out = json.load(f)
        cls.areas = os.path.join(cls.tmp, "areas")
        r = run_builder("build_areas.py", "--db", cls.db, "--out", cls.areas,
                        "--islands", os.path.join(FIX, "islands.csv"),
                        *cls.refs, "--col-mask-map", cls.col_mask)
        assert r.returncode == 0, r.stderr
        with open(os.path.join(cls.areas, "testisle.json")) as f:
            cls.area = json.load(f)

    def test_two_legacy_keys_count_as_one_species(self):
        self.assertEqual(self.out["totals"]["species"], 2)      # merged CRL7 + unmapped 9999
        self.assertEqual([s["n"] for s in self.area["species"]], ["Candidus merus", "Neminem novi"])
        merged = self.area["species"][0]
        self.assertEqual((merged["c"], merged["f"], merged["r"]), ("CRL7", "Candidaceae", 2))
        self.assertEqual([s["c"] for s in self.area["species"]], ["CRL7", None])   # col key field, nullable

    def test_col_lumped_leads_list_once_with_the_col_key(self):
        self.assertEqual(len(self.out["leads"]), 1)
        self.assertEqual(self.out["leads"][0][0], "Duo prima")
        self.assertEqual(self.out["leads"][0][2], 50)           # neighbour occurrences summed
        self.assertEqual(self.out["leads"][0][5], "CRL14")      # appended COL key element
        self.assertEqual(self.out["lead_totals"]["candidates"], 1)


class GateColMasking(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()

    def gate(self, **kw):
        return gate_with(self.tmp, **kw)

    def test_gate_masks_by_col_key_with_a_different_legacy_key(self):
        gate = self.gate(col_mask=os.path.join(FIX, "col_mask_map.csv"))
        self.assertEqual(gate.classify(species="Alius nomens", species_key="12345", col_key="COLK1"), "key")

    def test_gate_masks_col_accepted_names(self):
        gate = self.gate(col_mask=os.path.join(FIX, "col_mask_map.csv"))
        self.assertEqual(gate.classify(species="Matsutakus verus"), "name")   # COL name of a masked taxon
        self.assertIsNone(gate.classify(species="Neminem novi"))

    def test_missing_or_empty_col_mask_map_blocks_in_col_mode(self):
        with self.assertRaisesRegex(PublicTaxaError, "col_mask_map"):
            self.gate(col_mask=os.path.join(self.tmp, "nope.csv"))
        empty = os.path.join(self.tmp, "empty.csv")
        open(empty, "w").write("source,value,col_key,col_accepted_name\n")
        with self.assertRaisesRegex(PublicTaxaError, "col_mask_map"):
            self.gate(col_mask=empty)

    def test_sql_where_masks_col_keys(self):
        gate = self.gate(col_mask=os.path.join(FIX, "col_mask_map.csv"))
        con = duckdb.connect()
        gate.install_sql(con)
        con.execute("create table jp(speciesKey varchar, species varchar, genus varchar, "
                    "taxonRank varchar, col_key varchar)")
        con.execute(f"insert into jp values ('12345', 'Alius nomens', 'Alius', 'SPECIES', 'COLK1')")
        n = con.execute(f"select count(*) from jp where {gate.sql_where('jp')}").fetchone()[0]
        con.close()
        self.assertEqual(n, 0)   # masked by col_key despite the unrelated legacy key/name


class EmitMaskMap(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.gate = gate_with(self.tmp)
        self.con = duckdb.connect()

    def tearDown(self):
        self.con.close()

    def emit(self, drop=(), overrides=None):
        tsv = write_tsv_gz(os.path.join(self.tmp, "col.tsv.gz"), XWALK, drop=drop)
        col_xr.load_map(self.con, tsv)
        out = os.path.join(self.tmp, "col_mask_map.csv")
        col_xr.emit_mask_map(self.con, self.gate, out, overrides=overrides)
        with open(out, newline="") as f:
            return list(csv.DictReader(ln for ln in f if not ln.startswith("#")))

    def overrides(self, rows):
        path = os.path.join(self.tmp, "col_mask_overrides.csv")
        with open(path, "w", newline="", encoding="utf-8") as f:
            w = csv.writer(f, lineterminator="\n")
            w.writerow(["kind", "value", "col_key", "method", "reason"])
            w.writerows(rows)
        return path

    def test_emits_every_gate_key_and_name(self):
        rows = {(r["source"], r["value"]): (r["col_key"], r["col_accepted_name"]) for r in self.emit()}
        self.assertEqual(rows[("key", "9001")], ("CRL1", "Rareus japonicus"))
        self.assertEqual(rows[("name", "matsutakus fictus")], ("CRL3", "Matsutakus fictus"))
        self.assertEqual(rows[("name", "maskedus apocryphus")], ("CRL4", "Maskedus apocryphus"))  # authored row too
        self.assertEqual(rows[("name", "dubius ambiguus")], ("CRL2", "Dubius ambiguus"))          # ambiguous name
        self.assertNotIn(("key", "9003"), rows)   # exception-ignored: not gate-loaded

    def test_unmatched_key_or_name_fails_closed(self):
        for drop, what in ((("9001",), "key 9001"), (("7003",), "armillaria ficta")):
            with self.assertRaisesRegex(PublicTaxaError, what):
                self.emit(drop=drop)

    def test_override_with_col_key_joins_the_map(self):
        # 7002 dropped: "tricholoma fictum" misses, but its CRL3 accepted taxon stays
        # reachable via 7001, so the map row carries the accepted name; CRLX1/CRLX2
        # are in no crosswalk row, so those overrides map with an empty accepted name.
        # Dropping 9001 orphans the key AND the name "rareus japonicus".
        ov = self.overrides([("name", "tricholoma fictum", "CRL3", "api-name", "matched by name"),
                             ("key", "9001", "CRLX1", "api-key", "matched by usage key"),
                             ("name", "rareus japonicus", "CRLX2", "api-name", "matched by name")])
        rows = {(r["source"], r["value"]): (r["col_key"], r["col_accepted_name"])
                for r in self.emit(drop=("7002", "9001"), overrides=ov)}
        self.assertEqual(rows[("name", "tricholoma fictum")], ("CRL3", "Matsutakus fictus"))
        self.assertEqual(rows[("key", "9001")], ("CRLX1", ""))
        self.assertEqual(rows[("name", "rareus japonicus")], ("CRLX2", ""))

    def test_override_with_empty_col_key_passes_without_adding_a_key(self):
        ov = self.overrides([("name", "armillaria ficta", "", "manual", "COL lacks the taxon")])
        rows = {(r["source"], r["value"]): (r["col_key"], r["col_accepted_name"])
                for r in self.emit(drop=("7003",), overrides=ov)}
        self.assertEqual(rows[("name", "armillaria ficta")], ("", ""))   # reviewed legacy-masked

    def test_unmatched_entry_with_no_override_row_still_raises(self):
        ov = self.overrides([("name", "neminem novi", "CRL5", "manual", "unrelated row")])
        for bad in (None, ov):
            with self.assertRaisesRegex(PublicTaxaError, "key 9001"):
                self.emit(drop=("9001",), overrides=bad)
        with self.assertRaisesRegex(PublicTaxaError, "col_mask_overrides missing"):
            self.emit(drop=("9001",), overrides=os.path.join(self.tmp, "missing.csv"))

    def test_stale_override_row_raises(self):
        # Checked only when overrides are consumed at all: every real miss is
        # covered, but a row nothing asked for must not survive a review silently.
        ov = self.overrides([("name", "armillaria ficta", "", "manual", "COL lacks the taxon"),
                             ("key", "9001", "CRLX1", "api-key", "crosswalk resolves it now")])
        with self.assertRaisesRegex(PublicTaxaError, "no longer needed"):
            self.emit(drop=("7003",), overrides=ov)

    def test_malformed_override_rows_raise(self):
        for rows, what in (([("phylum", "9001", "CRLX1", "api-key", "")], "kind 'phylum'"),
                           ([("key", "9001", "CRLX1", "guess", "")], "method 'guess'"),
                           ([("key", "9001", "CRLX1", "api-key", ""), ("key", "9001", "", "manual", "")],
                            "duplicate")):
            with self.assertRaisesRegex(PublicTaxaError, what):
                self.emit(drop=("9001",), overrides=self.overrides(rows))

    def test_fixture_overrides_file_shape(self):
        rows = {(r["source"], r["value"]): (r["col_key"], r["col_accepted_name"])
                for r in self.emit(drop=("9001", "7003"),
                                   overrides=os.path.join(FIX, "col_mask_overrides.csv"))}
        self.assertEqual(rows[("key", "9001")], ("CRLX1", ""))
        self.assertEqual(rows[("name", "rareus japonicus")], ("CRLX2", ""))
        self.assertEqual(rows[("name", "armillaria ficta")], ("", ""))


if __name__ == "__main__":
    unittest.main()
