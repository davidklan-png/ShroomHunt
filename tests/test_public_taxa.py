"""TASK-002: shared protected-taxon gate — unit tests on fictional taxa.

Fixture inputs under tests/fixtures/reference/. The coverage manifest is
generated from the fixture Red List via coverage_row(), the same way the real
one is produced for review.
"""
import csv, os, sys, tempfile, unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from pipeline.public_taxa import PublicTaxaError, coverage_row, load_gate

FIX = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fixtures", "reference")


def write_coverage(directory, redlist=None, **override):
    """A coverage manifest row matching the fixture Red List (or overridden fields)."""
    row = coverage_row(redlist or os.path.join(FIX, "redlist.json"))
    row.update({k: str(v) for k, v in override.items()})
    path = os.path.join(directory, "redlist_coverage.csv")
    with open(path, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["sha256", "names", "matched", "ambiguous"])
        w.writeheader()
        w.writerow(row)
    return path


def gate_with(tmpdir, **kw):
    """load_gate against the fixtures, with per-test overrides (paths usually in tmpdir)."""
    args = dict(mask=os.path.join(FIX, "taxon_mask.csv"),
                exceptions=os.path.join(FIX, "match_exceptions.csv"),
                redlist=os.path.join(FIX, "redlist.json"))
    if "coverage" not in kw:
        args["coverage"] = write_coverage(tmpdir)
    args.update(kw)
    return load_gate(**args)


class GateBehaviour(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp()
        cls.gate = gate_with(cls.tmp)

    def test_accepted_and_alias_names_mask_without_keys(self):
        for name in ("Matsutakus fictus", "Tricholoma fictum", "Armillaria ficta"):
            self.assertEqual(self.gate.classify(species=name), "name")

    def test_key_match_with_unrelated_verbatim_name(self):
        for key in (9001, "9001"):
            self.assertEqual(self.gate.classify(species="Innocens alia", species_key=key), "key")

    def test_infraspecific_null_key_matches_binomial_prefix(self):
        self.assertEqual(self.gate.classify(species="Rareus japonicus var. albus",
                                            genus="Rareus", taxon_rank="VARIETY"), "name")
        # prefix needs the space: a different species of the same genus stays public
        self.assertIsNone(self.gate.classify(species="Rareus japonicusfalsus",
                                             genus="Rareus", taxon_rank="SPECIES"))

    def test_ambiguous_redlist_match_quarantined_by_name(self):
        self.assertEqual(self.gate.classify(species="Dubius ambiguus"), "ambiguous")

    def test_match_exception_ignore_makes_name_and_key_public(self):
        self.assertIsNone(self.gate.classify(species="Publicus vulgaris"))
        self.assertIsNone(self.gate.classify(species="Publicus vulgaris", species_key=9003))

    def test_match_exception_alias_masks(self):
        self.assertEqual(self.gate.classify(species="Boletus fictus"), "name")

    def test_genus_only_row_in_masked_genus_quarantined(self):
        self.assertEqual(self.gate.classify(genus="Matsutakus", taxon_rank="GENUS"), "genus_only")
        self.assertIsNone(self.gate.classify(genus="Cleanus", taxon_rank="GENUS"))
        # a fully identified different species of a masked genus is public
        self.assertIsNone(self.gate.classify(species="Matsutakus publicus", species_key=555,
                                             genus="Matsutakus", taxon_rank="SPECIES"))
        # rank None is quarantined like sql_where() does, not treated as species rank
        self.assertEqual(self.gate.classify(species="Matsutakus publicus", species_key=555,
                                            genus="Matsutakus", taxon_rank=None), "genus_only")

    def test_audit_counts_are_aggregate_only(self):
        rows = [("Matsutakus fictus", "7001", "Matsutakus", "SPECIES"),
                ("Tricholoma fictum", None, "Tricholoma", "SPECIES"),
                (None, None, "Matsutakus", "GENUS"),
                ("Rareus japonicus", "9001", "Rareus", "SPECIES"),
                ("Dubius ambiguus", None, "Dubius", "SPECIES"),
                ("Publicus bonum", "7002", "Publicus", "SPECIES")]
        counts = self.gate.audit(rows)
        self.assertEqual(counts, {"public": 1, "masked_key": 1, "masked_name": 2,
                                  "quarantined_ambiguous": 1, "quarantined_genus_only": 1})
        self.assertTrue(all(isinstance(k, str) and isinstance(v, int) for k, v in counts.items()))


class FailClosed(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()

    def test_missing_mask_blocks(self):
        with self.assertRaises(PublicTaxaError):
            gate_with(self.tmp, mask=os.path.join(self.tmp, "nope.csv"))

    def test_empty_mask_blocks(self):
        p = os.path.join(self.tmp, "empty_mask.csv")
        open(p, "w").write("scientific_name,aliases,reason\n# only comments\n")
        with self.assertRaises(PublicTaxaError):
            gate_with(self.tmp, mask=p)

    def test_missing_redlist_blocks(self):
        with self.assertRaises(PublicTaxaError):
            gate_with(self.tmp, redlist=os.path.join(self.tmp, "nope.json"))

    def test_missing_coverage_blocks(self):
        with self.assertRaises(PublicTaxaError):
            gate_with(self.tmp, coverage=os.path.join(self.tmp, "nope.csv"))

    def test_unreviewed_seed_coverage_blocks(self):
        p = os.path.join(self.tmp, "seed.csv")
        open(p, "w").write("# seed\nsha256,names,matched,ambiguous\n")
        with self.assertRaises(PublicTaxaError):
            gate_with(self.tmp, coverage=p)

    def test_coverage_digest_mismatch_blocks(self):
        with self.assertRaisesRegex(PublicTaxaError, "sha256"):
            gate_with(self.tmp, coverage=write_coverage(self.tmp, sha256="0" * 64))

    def test_coverage_count_mismatch_blocks(self):
        with self.assertRaisesRegex(PublicTaxaError, "names"):
            gate_with(self.tmp, coverage=write_coverage(self.tmp, names="99"))

    def test_coverage_any_appended_row_covers_download(self):
        # rows are appended after each reviewed download; an older stale row must not block
        p = os.path.join(self.tmp, "coverage.csv")
        row = coverage_row(os.path.join(FIX, "redlist.json"))
        with open(p, "a") as f:
            f.write("sha256,names,matched,ambiguous\n")
            f.write("0" * 64 + ",1,1,0\n")  # stale row from a previous download
            f.write(",".join(row.values()) + "\n")
        gate_with(self.tmp, coverage=p)  # no PublicTaxaError

    def test_unknown_exception_action_blocks(self):
        p = os.path.join(self.tmp, "bad_exceptions.csv")
        open(p, "w").write("name,action,note\nFungus x,frobnicate,nope\n")
        with self.assertRaisesRegex(PublicTaxaError, "frobnicate"):
            gate_with(self.tmp, exceptions=p)


if __name__ == "__main__":
    unittest.main()
