"""TASK-006: the v1 public contract — schema_version + one bundle snapshot
(pipeline/snapshot.py), pipeline/validate_dashboard_publish.py as the
publication gate, the shared source-precision eligibility moved to
pipeline/eligibility.py, and the publish workflow's build -> stamp ->
validate -> copy ordering.

The fixture bundle is built exactly the way publish-dashboard.yml builds it
(plan §11): raw TSVs -> build_db.py -> build_dashboard_data.py ->
build_areas.py -> snapshot.py, against the fictional taxa and islands of
test_public_taxa_outputs.py / test_area_sanitization.py. Every reject reason
is then provoked by one deliberate mutation of a copy of that bundle.
"""
import gzip, json, os, random, re, shutil, subprocess, sys, tempfile, unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from pipeline.eligibility import spatially_eligible  # noqa: E402
from pipeline.geo_ja import cell_center, mesh_cell  # noqa: E402
from pipeline.snapshot import bundle_digest, canonical, write_json  # noqa: E402
from test_public_taxa import FIX, write_coverage  # noqa: E402
from test_public_taxa_outputs import (MO_ROWS, NB_ROWS, run_builder)  # noqa: E402
from test_area_sanitization import (EA_ROWS, ISLANDS_CSV, JP_COLUMNS, JP_ROWS,  # noqa: E402
                                    XE_ROWS)
from test_build_map_reference import build_fixture  # noqa: E402

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
WORKFLOW = os.path.join(REPO, ".github", "workflows", "publish-dashboard.yml")

JP_HEADER = ["gbifID", *[c.split()[0] for c in JP_COLUMNS.split(", ")],
             "coordinateUncertaintyInMeters", "coordinatePrecision", "issue"]
NB_HEADER = ["species", "phylum", "order", "family", "genus", "class", "numberOfOccurrences",
             "iucnRedListCategory", "taxonRank", "taxonomicStatus", "speciesKey"]

# the eligible whole cells the 1B fixture DB owns (see test_area_sanitization):
# dashboard mesh and index cells must both be exactly this set — the flagged
# cells (315:1021, 318:1022, ...) appear in neither.
ELIGIBLE_CELLS = {"313:1021", "317:1022", "322:1025"}

RESEARCH = {"schema_version": 1, "snapshot": "",
            "slices": [[0, "all", [["313:1021", 1, None], ["322:1025", 2, 0]]]],
            # TASK-008: the optional third element carries Name-search strings; both
            # arities stay legal, anything else is an unknown shape.
            "genera": [["Prosus", ["okisle"]], ["Russula", ["okisle"], ["brittlegills", "ベニタケ属"]]]}


def tsv(path, header, rows):
    with open(path, "w", newline="\n") as f:
        f.write("\t".join(header) + "\n")
        for r in rows:
            f.write("\t".join("" if v is None else str(v) for v in r) + "\n")
    return path


class PublishContract(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp()
        cls.refs = ["--mask", os.path.join(FIX, "taxon_mask.csv"),
                    "--coverage", write_coverage(cls.tmp),
                    "--exceptions", os.path.join(FIX, "match_exceptions.csv"),
                    "--redlist", os.path.join(FIX, "redlist.json"),
                    "--col-mask-map", os.path.join(FIX, "col_mask_map.csv")]
        raw = os.path.join(cls.tmp, "raw")
        os.makedirs(raw)
        tsv(os.path.join(raw, "jp.tsv"), JP_HEADER, JP_ROWS)
        tsv(os.path.join(raw, "nb.tsv"), NB_HEADER, NB_ROWS)
        tsv(os.path.join(raw, "mo.tsv"), ["speciesKey", "countryCode"], MO_ROWS)
        tsv(os.path.join(raw, "dwca.tsv"),
            ["gbifID", "habitat", "associatedTaxa", "elevation", "dataGeneralizations",
             "informationWithheld"], XE_ROWS)
        tsv(os.path.join(raw, "ea.tsv"), ["speciesKey", "species", "family", "decimalLatitude",
                                          "decimalLongitude", "month"],
            [[k, sp, fam, lat, lon, mon] for k, sp, fam, lat, lon, mon, n in EA_ROWS for _ in range(n)])
        meta = os.path.join(raw, "meta.json")
        with open(meta, "w") as f:
            json.dump({"key": "dx", "doi": "10.9999/fake-doi", "totalRecords": 13,
                       "created": "2026-11-21T00:00:00Z"}, f)
        cls.islands = os.path.join(cls.tmp, "islands.csv")
        with open(cls.islands, "w") as f:
            f.write(ISLANDS_CSV)
        # the three workflow builder commands (§11), into a scratch DB and staging dir
        cls.db = os.path.join(cls.tmp, "scratch.duckdb")
        cls.staging = os.path.join(cls.tmp, "staging")
        os.makedirs(cls.staging)
        r = run_builder("build_db.py", "--japan", os.path.join(raw, "jp.tsv"),
                        "--neighbours", os.path.join(raw, "nb.tsv"),
                        "--mo", os.path.join(raw, "mo.tsv"),
                        "--japan-dwca", os.path.join(raw, "dwca.tsv"),
                        "--eastasia", os.path.join(raw, "ea.tsv"), "--db", cls.db)
        assert r.returncode == 0, r.stdout + r.stderr
        r = run_builder("build_dashboard_data.py", "--db", cls.db,
                        "--out", os.path.join(cls.staging, "dashboard.json"),
                        "--meta", meta, *cls.refs)
        assert r.returncode == 0, r.stdout + r.stderr
        r = run_builder("build_areas.py", "--db", cls.db,
                        "--out", os.path.join(cls.staging, "areas"),
                        "--islands", cls.islands, *cls.refs)
        assert r.returncode == 0, r.stdout + r.stderr
        cls.unstamped = os.path.join(cls.tmp, "unstamped")
        shutil.copytree(cls.staging, cls.unstamped)          # the builders' pre-stamp bytes
        r = run_builder("snapshot.py", cls.staging)
        assert r.returncode == 0, r.stdout + r.stderr
        _, ref = build_fixture()                              # 0E reference geography asset
        for p in ref["places"]:            # its CSV anchors the real okinawa-honto slug; this
            if p.get("area_id"):           # fictional bundle owns okisle/lilisle/t4028 instead
                p["area_id"] = "okisle"
        write_json(os.path.join(cls.staging, "japan-outline.json"), ref)

    @classmethod
    def validate(cls, path, *extra):
        return subprocess.run([sys.executable, os.path.join(REPO, "pipeline",
                                                            "validate_dashboard_publish.py"),
                               path, *cls.refs, *extra], capture_output=True, text=True, cwd=REPO)

    def copy(self):
        path = os.path.join(tempfile.mkdtemp(prefix="reject-"), "staging")
        shutil.copytree(self.staging, path)
        return path

    def edit(self, path, rel, fn):
        p = os.path.join(path, rel)
        with open(p) as f:
            d = json.load(f)
        fn(d)
        write_json(p, d)

    def reject(self, mutate, *needles, restamp=False):
        """Mutate a copy of the green bundle; the validator must exit non-zero naming each needle."""
        path = self.copy()
        mutate(path)
        if restamp:
            r = run_builder("snapshot.py", path)
            assert r.returncode == 0, r.stdout + r.stderr
        r = self.validate(path)
        self.assertNotEqual(r.returncode, 0, f"unexpectedly publishable:\n{r.stdout}")
        for needle in needles:
            self.assertIn(needle, r.stderr, f"{needle!r} missing from:\n{r.stderr}")
        return r.stderr

    # ---------- the green path ----------------------------------------------------------------

    def test_built_bundle_validates(self):
        r = self.validate(self.staging)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("publishable", r.stdout)
        self.assertIn("8 files", r.stdout)      # dashboard, index, 2 islands, tile, 2 groups, outline

    def test_builders_emit_v1_shapes_with_empty_snapshot_then_stamp(self):
        with open(os.path.join(self.unstamped, "dashboard.json")) as f:
            d = json.load(f)
        self.assertEqual(d["schema_version"], 1)
        self.assertEqual(d["snapshot"], "")
        with open(os.path.join(self.staging, "dashboard.json")) as f:
            stamped = json.load(f)
        self.assertRegex(stamped["snapshot"], r"^[0-9a-f]{64}$")
        digest = bundle_digest(self.staging)
        for rel in os.listdir(os.path.join(self.staging, "areas")) + ["dashboard.json"]:
            with open(os.path.join(self.staging, "dashboard.json" if rel == "dashboard.json"
                                   else os.path.join("areas", rel))) as f:
                self.assertEqual(json.load(f)["snapshot"], digest, rel)
        with open(os.path.join(self.staging, "japan-outline.json")) as f:
            self.assertNotIn("snapshot", json.load(f))       # geography versions independently

    def test_published_bytes_are_canonical_sorted_lf(self):
        for root, _, names in os.walk(self.staging):
            for n in names:
                p = os.path.join(root, n)
                with open(p, "rb") as f:
                    raw = f.read()
                self.assertTrue(raw.endswith(b"\n") and b"\r" not in raw, p)
                self.assertEqual(raw, (canonical(json.loads(raw)) + "\n").encode(), p)

    def test_shared_eligibility_applies_to_both_outputs(self):
        with open(os.path.join(self.staging, "dashboard.json")) as f:
            dash = json.load(f)
        with open(os.path.join(self.staging, "areas", "index.json")) as f:
            ix = json.load(f)
        mesh_keys = {f"{mesh_cell(clat, clon)[0]}:{mesh_cell(clat, clon)[1]}"
                     for clat, clon, _, _ in dash["mesh"]}
        self.assertEqual(mesh_keys, ELIGIBLE_CELLS)           # flagged-only cells never appear
        self.assertEqual(set(ix["cells"]), ELIGIBLE_CELLS)    # ...in either output
        self.assertNotIn("315:1021", mesh_keys)               # uncertainty 25 km
        self.assertNotIn("318:1023", mesh_keys)               # withheld/generalized notes

    def test_spatially_eligible_in_its_shared_home(self):
        self.assertTrue(spatially_eligible(None, None, "COORDINATE_ROUNDED", None, None))
        self.assertFalse(spatially_eligible(25000, None, None, None, None))
        self.assertFalse(spatially_eligible(None, 0.5, None, None, None))
        self.assertFalse(spatially_eligible(None, None, "PRESUMED_SWAPPED_COORDINATE", None, None))
        self.assertFalse(spatially_eligible(None, None, None, "coordinates rounded to 0.1", None))
        self.assertFalse(spatially_eligible(None, None, None, None, "location obscured"))

    def test_research_is_optional_but_joins_the_snapshot(self):
        path = self.copy()
        write_json(os.path.join(path, "mesh-research.json"), RESEARCH)
        r = self.validate(path)
        self.assertNotEqual(r.returncode, 0)                  # stale stamps: the digest moved
        self.assertIn("digest mismatch", r.stderr)
        r = run_builder("snapshot.py", path)
        assert r.returncode == 0, r.stdout + r.stderr
        r = self.validate(path)
        self.assertEqual(r.returncode, 0, r.stderr)
        with open(os.path.join(path, "dashboard.json")) as f:
            self.assertNotEqual(json.load(f)["snapshot"],
                                bundle_digest(self.staging))  # optional data invalidates joins

    def test_research_genera_names_must_be_strings(self):
        # TASK-008: the optional third element is a list of strings and nothing else.
        for mutate in (lambda row: row.__setitem__(2, "brittlegills"),   # not a list
                       lambda row: row.__setitem__(2, ["ok", 7])):       # non-string member
            research = json.loads(json.dumps(RESEARCH))
            mutate(research["genera"][1])
            def put(p, research=research):
                write_json(os.path.join(p, "mesh-research.json"), research)
            self.reject(put, "genera[1]", restamp=True)

    def test_research_genera_rejects_unknown_arities(self):
        research = json.loads(json.dumps(RESEARCH))
        research["genera"][1].append("extra")                            # a fourth element
        def put(p):
            write_json(os.path.join(p, "mesh-research.json"), research)
        self.reject(put, "genera[1]", restamp=True)

    def test_id_depth_rejects_duplicate_or_missing_buckets(self):
        # PR #5 review: [species, species, genus] with a correct sum must not publish
        def put(d):
            s, g, h = (n for _, n in d["id_depth"])
            d["id_depth"] = [["species", s], ["species", 0], ["genus", g + h]]
        self.reject(lambda p: self.edit(p, "areas/okisle.json", put),
                    "species/genus/higher triple", restamp=True)

    def test_group_aggregates_reject_when_they_contradict_their_members(self):
        # PR #5 review: moving counts between bases keeps every total but contradicts
        # the leaf areas — the inspector would show a chart no island supports
        def put(d):
            ev = {k: n for k, n in d["evidence"]}
            for k in list(ev):
                if k != "HUMAN_OBSERVATION":
                    ev["HUMAN_OBSERVATION"] = ev.get("HUMAN_OBSERVATION", 0) + ev.pop(k)
            d["evidence"] = sorted(ev.items(), key=lambda kv: -kv[1])
        self.reject(lambda p: self.edit(p, "areas/g-okinawa-prefecture.json", put),
                    "disagrees with the sum of its members", restamp=True)

    # ---------- the reject matrix (one deliberate fault per variant) ---------------------------

    def test_unknown_schema_version_rejects(self):
        self.reject(lambda p: self.edit(p, "dashboard.json",
                                        lambda d: d.update(schema_version=2)), "schema_version")

    def test_missing_snapshot_rejects(self):
        self.reject(lambda p: self.edit(p, "dashboard.json",
                                        lambda d: d.pop("snapshot")), "snapshot")

    def test_digest_mismatch_rejects(self):
        self.reject(lambda p: self.edit(p, "dashboard.json",
                                        lambda d: d["totals"].update(records=d["totals"]["records"] + 1)),
                    "digest mismatch")

    def test_stray_file_rejects(self):
        def stray(p):
            write_json(os.path.join(p, "notes.json"), {"oops": True})
        self.reject(stray, "stray file")

    def test_missing_area_file_rejects(self):
        self.reject(lambda p: os.remove(os.path.join(p, "areas", "okisle.json")),
                    "missing area file")

    def test_mismatched_key_sets_reject(self):
        def extra_cell(p):
            self.edit(p, "dashboard.json",
                      lambda d: d["mesh"].append([*cell_center(400, 1100), 1, 1]))
        self.reject(extra_cell, "mesh/index key mismatch", restamp=True)

    def test_masked_taxon_in_species_list_rejects(self):
        def masked(p):
            self.edit(p, "areas/okisle.json", lambda d: d["species"].append(
                {"n": "Tricholoma fictum", "c": None, "f": "Fictaceae", "k": "m", "r": 1, "m": [0] * 12,
                 "sq": 0, "y": None, "e": {}, "h": [], "l": [], "nm": 0, "el": None}))
        self.reject(masked, "protected taxon", restamp=True)

    def test_masked_taxon_in_leads_rejects(self):
        def masked(p):
            self.edit(p, "dashboard.json",
                      lambda d: d["leads"].append(["Dubius ambiguus", None, 40, 1, "genus known in Japan", None]))
        self.reject(masked, "protected taxon", restamp=True)

    def test_masked_col_key_in_species_list_rejects(self):
        # TASK-018: a masked COL key in the additive "c" field fails validation even
        # when the name is an innocent synonym the backbone never listed.
        def masked(p):
            self.edit(p, "areas/okisle.json", lambda d: d["species"][0].update(c="COLK1"))
        self.reject(masked, "protected taxon", "masked col_key", restamp=True)

    def test_prose_field_rejects(self):
        def prose(p):
            self.edit(p, "areas/okisle.json", lambda d: d["species"][0].update(h=["on dead Quercus wood"]))
        self.reject(prose, "unsafe field", restamp=True)

    def test_prose_pattern_in_a_surviving_string_rejects(self):
        def prose(p):
            self.edit(p, "areas/okisle.json", lambda d: d.update(name_en="Hidden Corner Meadow"))
        self.reject(prose, "unsafe field", "known prose pattern", restamp=True)

    def test_dominant_source_shape_rejects(self):
        # TASK-012: the optional field is [title, share] with share >= 0.30; anything
        # else — a share below the threshold, a non-string title, a wrong arity — rejects.
        for bad in (["Survey", 0.1], ["Survey", True], [7, 0.5], ["Survey"], ["Survey", 0.5, "x"]):
            def put(p, bad=bad):
                self.edit(p, "areas/okisle.json", lambda d: d.update(dominant_source=bad))
            self.reject(put, "dominant_source", restamp=True)
        # ... and even a well-formed pair rejects on a group file: the builder
        # emits dominant_source per sampling area (island/tile), never per group.
        def on_group(p):
            with open(os.path.join(p, "areas", "index.json")) as f:
                gid = next(r["id"] for r in json.load(f)["areas"] if r["kind"] == "group")
            self.edit(p, f"areas/{gid}.json", lambda d: d.update(dominant_source=["Survey", 0.5]))
        self.reject(on_group, "dominant_source", restamp=True)

    def test_area_aggregates_stay_optional_bundle_wide(self):
        # TASK-021: additive trio, schema_version stays 1 — a bundle from the
        # pre-TASK-021 builder (no area carrying them) remains publishable
        path = self.copy()
        for rel in os.listdir(os.path.join(path, "areas")):
            if rel != "index.json":
                self.edit(path, f"areas/{rel}",
                          lambda d: [d.pop(k) for k in ("evidence", "id_depth", "decades")])
        r = run_builder("snapshot.py", path)
        assert r.returncode == 0, r.stdout + r.stderr
        r = self.validate(path)
        self.assertEqual(r.returncode, 0, r.stderr)

    def test_area_aggregates_partial_trio_or_partial_bundle_reject(self):
        def drop_decades(p):
            self.edit(p, "areas/okisle.json", lambda d: d.pop("decades"))
        self.reject(drop_decades, "key set", restamp=True)
        def strip_one_area(p):
            self.edit(p, "areas/lilisle.json",
                      lambda d: [d.pop(k) for k in ("evidence", "id_depth", "decades")])
        self.reject(strip_one_area, "all or none", restamp=True)

    def test_area_aggregate_shapes_reject(self):
        for bad in ([[7, 1]],                                        # basis is neither string nor null
                    [["HUMAN_OBSERVATION"]],                         # wrong arity
                    [["HUMAN_OBSERVATION", "3"]],                    # count is not a UInt
                    [["HUMAN_OBSERVATION", -1]],                     # negative count
                    [["HUMAN_OBSERVATION", 1], "row"]):              # a row is not a pair
            def put(p, bad=bad):
                self.edit(p, "areas/okisle.json", lambda d: d.update(evidence=bad))
            self.reject(put, "evidence[", restamp=True)
        for field, bad, needle in (("id_depth", [["family", 1]], "id_depth[0]"),
                                   ("decades", [["1990s", 1]], "decades[0]"),
                                   ("decades", [[1990.5, 1]], "decades[0]")):
            def put(p, field=field, bad=bad):
                self.edit(p, "areas/okisle.json", lambda d: d.update({field: bad}))
            self.reject(put, needle, restamp=True)

    def test_area_aggregate_wrong_type_rejects(self):
        self.reject(lambda p: self.edit(p, "areas/okisle.json", lambda d: d.update(evidence={})),
                    "wrong type", restamp=True)

    def test_area_aggregate_sum_mismatches_reject(self):
        def bump_evidence(p):
            self.edit(p, "areas/okisle.json",
                      lambda d: d["evidence"][0].__setitem__(1, d["evidence"][0][1] + 1))
        self.reject(bump_evidence, "evidence does not sum to totals.records", restamp=True)
        def bump_depth(p):
            self.edit(p, "areas/okisle.json",
                      lambda d: d["id_depth"][0].__setitem__(1, d["id_depth"][0][1] + 1))
        self.reject(bump_depth, "id_depth does not sum to totals.records", restamp=True)
        def pad_decades(p):
            self.edit(p, "areas/okisle.json", lambda d: d["decades"].append([2030, 5]))
        self.reject(pad_decades, "decades sums above totals.records", restamp=True)

    def test_over_budget_file_rejects(self):
        def pad(p):
            def fn(d):
                rng = random.Random(6)
                while len(gzip.compress(canonical(d).encode(), 9)) <= 22 * 1024:
                    d["sources"].append({"key": f"s{len(d['sources'])}",
                                         "doi": "10.9999/" + "".join(rng.choice("0123456789abcdef")
                                                                     for _ in range(30)),
                                         "records": None, "created": None})
            self.edit(p, "dashboard.json", fn)
        self.reject(pad, "over budget", restamp=True)

    def test_bad_rectangle_rejects(self):
        def shift(p):
            self.edit(p, "dashboard.json", lambda d: d["mesh"][0].__setitem__(0, d["mesh"][0][0] + 0.0001))
        self.reject(shift, "rectangle", restamp=True)

    def test_unreadable_gate_inputs_reject(self):
        r = self.validate(self.staging, "--redlist", os.path.join(self.tmp, "nope.json"))
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("protected-taxon gate", r.stderr)

    # ---------- geography-only bundle ----------------------------------------------------------

    def test_geography_only_bundle_validates(self):
        path = os.path.join(tempfile.mkdtemp(prefix="geo-"), "staging")
        os.makedirs(path)
        _, ref = build_fixture()
        write_json(os.path.join(path, "japan-outline.json"), ref)
        r = self.validate(path)
        self.assertEqual(r.returncode, 0, r.stderr)

    def test_geography_only_rejects_stray_and_unknown_version(self):
        path = os.path.join(tempfile.mkdtemp(prefix="geo-"), "staging")
        os.makedirs(path)
        _, ref = build_fixture()
        ref = dict(ref, schema_version=2)
        write_json(os.path.join(path, "japan-outline.json"), ref)
        write_json(os.path.join(path, "extra.json"), {})
        r = self.validate(path)
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("schema_version", r.stderr)
        self.assertIn("stray file", r.stderr)

    # ---------- the workflow: unsafe staging can never reach the copy step ---------------------

    def test_validator_sits_between_build_and_copy(self):
        with open(WORKFLOW) as f:
            text = f.read()
        steps = re.findall(r"^\s+- name: (.+)$", text, re.M)
        self.assertLess(steps.index("Build database and the v1 occurrence bundle into staging"),
                        steps.index("Validate staging bundle (publication gate)"))
        self.assertLess(steps.index("Build reference geography into staging (geography_only)"),
                        steps.index("Validate staging bundle (publication gate)"))
        self.assertLess(steps.index("Validate staging bundle (publication gate)"),
                        steps.index("Open pull request on Bio_HP"))
        gate = text[text.index("- name: Validate staging bundle (publication gate)"):
                    text.index("- name: Open pull request on Bio_HP")]
        self.assertNotIn("enable_public_pr", gate)            # validation always runs
        pr = text[text.index("- name: Open pull request on Bio_HP"):]
        self.assertIn("inputs.enable_public_pr == true", pr)  # the copy stays behind the opt-in
        markers = ("build_dashboard_data.py --out staging", "build_areas.py --out staging",
                   "snapshot.py staging", "validate_dashboard_publish.py staging",
                   "git clone", "cp staging/dashboard.json")
        at = [text.index(m) for m in markers]
        self.assertEqual(at, sorted(at), "build -> stamp -> validate -> clone/copy order broken")
        self.assertIn("Rollback", text)


if __name__ == "__main__":
    unittest.main()
