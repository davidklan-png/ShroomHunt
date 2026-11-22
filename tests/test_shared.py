"""TASK-015: pipeline/shared.py — kind() and specimen_key(), the two helpers the
area and research builders share. Pins the classification table and the pair
normalisation so neither home can drift again."""
import os, sys, unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from pipeline.shared import kind, specimen_key  # noqa: E402


class Kind(unittest.TestCase):
    def test_one_of_each_kind(self):
        self.assertEqual(kind("Lecanoromycetes", "Fictaceae", "Prosus"), "l")   # lichen by class
        self.assertEqual(kind("Dothideomycetes", "Verrucariaceae", "Prosus"), "l")  # lichen by family
        self.assertEqual(kind("Agaricomycetes", "Fictaceae", "Prosus"), "m")    # macro by class
        self.assertEqual(kind("Fictomycetes", "Fictaceae", "Tremella"), "m")    # macro by genus
        self.assertEqual(kind("Saccharomycetes", "Saccharomycetaceae", "Saccharomyces"), "o")  # other


class SpecimenKey(unittest.TestCase):
    def test_case_and_whitespace_fold_to_the_same_pair(self):
        self.assertEqual(specimen_key("S-NET", "AB 7"), ("s-net", "ab 7"))
        self.assertEqual(specimen_key(" s-net ", "ab  7"), ("s-net", "ab 7"))   # strip + collapse + casefold

    def test_empty_either_side_is_none(self):
        self.assertIsNone(specimen_key("", "AB 7"))
        self.assertIsNone(specimen_key("S-NET", ""))
        self.assertIsNone(specimen_key(None, "AB 7"))
        self.assertIsNone(specimen_key("S-NET", None))


if __name__ == "__main__":
    unittest.main()
