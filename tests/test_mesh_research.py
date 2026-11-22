"""TASK-007: build_mesh_research.py — the gated research payload (plan §5 row 3A,
§6 Research, §7 research gates).

Fictional taxa only, as in test_public_taxa_outputs.py. Every gate is proven
from both sides: a fixture that must ship and a neighbour one record/event/
cell short that must be withheld. The end-to-end test rebuilds the whole
fixture bundle the way publish-dashboard.yml does (build_db -> builders ->
research -> snapshot -> validate) with mesh-research.json in the digest.
"""
import csv, gzip, json, os, subprocess, sys, tempfile, unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import duckdb

from pipeline.snapshot import bundle_digest  # noqa: E402
from pipeline.public_taxa import load_gate  # noqa: E402
from test_public_taxa import FIX, write_coverage  # noqa: E402
from test_public_taxa_outputs import MO_ROWS, NB_ROWS, run_builder  # noqa: E402
from test_area_sanitization import ISLANDS_CSV  # noqa: E402

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
KIB = 1024

# all-varchar jp, the shape build_db.py produces (plus the event columns GBIF
# SIMPLE_CSV / the DwC-A carry); precision columns included so the shared
# eligibility is exercised alongside the research gates.
COLS = ("gbifID varchar, occurrenceID varchar, speciesKey varchar, species varchar, genus varchar, "
        "family varchar, taxonRank varchar, basisOfRecord varchar, class varchar, phylum varchar, "
        "decimalLatitude varchar, decimalLongitude varchar, month varchar, year varchar, "
        "locality varchar, stateProvince varchar, datasetKey varchar, eventID varchar, recordedBy varchar, "
        "coordinateUncertaintyInMeters varchar, coordinatePrecision varchar, issue varchar")
COLS_NO_GBIF = COLS.replace("gbifID varchar, ", "")

A, B, C, D = "313:1021", "314:1021", "315:1021", "316:1021"       # lat 26.1 / 26.2 / 26.3 / 26.4, lon 127.7
T1, MU = "317:1023", "318:1023"                                   # 26.45 / 26.55, lon 127.9
V, W, F = "342:1030", "343:1030", "348:1030"                      # 28.55 / 28.65 / 29.05, lon 128.76
E_KEYS = ["324:1030", "325:1030", "327:1030", "328:1030", "329:1030", "330:1030", "331:1030", "333:1030"]
S_KEYS = ["336:1030", "337:1030", "339:1030", "340:1030", "341:1030"]
PAUCIUS_CELL = "313:1022"                                          # inside the okisle island box

def rec(gbif, lat, lon, month, ev, rank="SPECIES", species="Prosus lautus", genus="Prosus",
        cls="Agaricomycetes", unc=None):
    return (gbif, None, None if rank != "SPECIES" else gbif.replace("-", ""), species if rank == "SPECIES" else None,
            genus, "Fictaceae", rank, "HUMAN_OBSERVATION", cls, "Basidiomycota", str(lat), str(lon),
            None if month is None else str(month), "2020", None, "Okinawa", "dx", ev, None, unc, None, None)

def grec(pref, gb, lat, lon, ev, genus):
    return rec(f"{pref}{gb}", lat, lon, 6, ev, rank="GENUS", genus=genus, cls="Fictomycetes")

GC = [(26.1, 127.8), (26.3, 127.8), (26.45, 127.8)]   # three cells whose centres are inside okisle
RC = [(30.05, 128.8), (30.15, 128.8), (30.25, 128.8)]  # separate cells so only Allowus pads GC cell 1

def fixture_rows():
    rows = []
    # cell A, month 3, kind m: 12 species + 5 genus-rank, 3 events (+1 duplicate, +1 ineligible)
    rows += [rec(f"A{i}", 26.1, 127.7, 3, f"e{i % 3 + 1}") for i in range(12)]
    rows += [rec(f"AG{i}", 26.1, 127.7, 3, f"e{i % 3 + 1}", rank="GENUS") for i in range(5)]
    rows.append(rows[0])                                        # exact duplicate source record: counted once
    rows.append(rec("Aflag", 26.1, 127.7, 3, "e1", unc="25000"))  # ineligible precision: never counted
    # B 9 records, C 10 records/2 events: below support everywhere; D exactly 10/3: ships
    rows += [rec(f"B{i}", 26.2, 127.7, 5, f"e{i % 3 + 1}") for i in range(9)]
    rows += [rec(f"C{i}", 26.3, 127.7, 5, f"e{i % 2 + 1}") for i in range(10)]
    rows += [rec(f"D{i}", 26.4, 127.7, 5, f"e{i % 3 + 1}") for i in range(10)]
    # T1: month 7 supported, month 8 below support -> month 0 keeps the cell
    # (one published month: the remainder spans 11 months, differencing cannot isolate it)
    rows += [rec(f"T7{i}", 26.45, 127.9, 7, f"e{i % 3 + 1}") for i in range(12)]
    rows += [rec(f"T8{i}", 26.45, 127.9, 8, f"e{i % 3 + 1}") for i in range(9)]
    # MU: 11 records with unknown month -> month-0 slices only
    rows += [rec(f"M{i}", 26.55, 127.9, None, f"e{i % 3 + 1}") for i in range(11)]
    # W: 9 unique records + 2 duplicate rows -> deduped to 9, below support everywhere
    w = [rec(f"W{i}", 28.65, 128.76, 11, f"e{i % 3 + 1}") for i in range(9)]
    rows += w + [w[0], w[0]]
    # V: 10 records month 9 + 15 month 10 -> each month band 0, together band 1 (25)
    rows += [rec(f"Va{i}", 28.55, 128.76, 9, f"e{i % 3 + 1}") for i in range(10)]
    rows += [rec(f"Vb{i}", 28.55, 128.76, 10, f"e{i % 3 + 1}") for i in range(15)]
    # F: 10 records / 3 events, one flagged -> 9 eligible: below support everywhere
    rows += [rec(f"F{i}", 29.05, 128.76, 1, f"e{i % 3 + 1}", unc="25000" if i == 0 else None)
             for i in range(10)]
    # count-band boundary cells: 24/25, 49/50, 99/100, 249/250
    for lat, n in zip((27.05, 27.15, 27.25, 27.35, 27.45, 27.55, 27.65, 27.75),
                      (24, 25, 49, 50, 99, 100, 249, 250)):
        rows += [rec(f"E{int(lat * 100)}-{i}", lat, 128.76, 2, f"e{i % 5}") for i in range(n)]
    # genus-share cells (month 4): both sides >=5 or the share is withheld
    for lat, gn, sn in ((28.05, 5, 20),   # exactly 20% -> band 1
                        (28.15, 5, 21),   # 19.2% -> band 0
                        (28.25, 20, 5),   # exactly 80% -> band 4
                        (28.35, 4, 6),    # genus side 4: share withheld, count band may ship
                        (28.45, 5, 5)):   # 50% -> band 2
        rows += [rec(f"S{int(lat * 100)}g{i}", lat, 128.76, 4, f"e{i % 5}", rank="GENUS") for i in range(gn)]
        rows += [rec(f"S{int(lat * 100)}s{i}", lat, 128.76, 4, f"e{i % 5}") for i in range(sn)]
    # genus-search fixtures: one genus per gate outcome (leaf area = okisle)
    for i, (lat, lon) in enumerate(GC):
        rows += [grec("AL", f"{i}-{j}", lat, lon, f"ev{j + 1}", "Allowus") for j in range(4)]   # ships
        rows += [grec("GH", f"{i}-{j}", lat, lon, f"gv{j + 1}", "Ghostus") for j in range(4)]   # unlisted
    for i, (lat, lon) in enumerate(RC):
        rows += [grec("RA", f"{i}-{j}", lat, lon, f"ev{j}", "Rarus") for j in range(3)]         # 9 records
        rows += [grec("EV", f"{i}-{j}", lat, lon, "ev0", "Evencus") for j in range(4)]          # 1 event/area
        rows += [grec("MA", f"{i}-{j}", lat, lon, f"ev{j}", "Matsutakus") for j in range(4)]    # masked genus
    rows += [grec("PA", str(j), 26.1, 127.8, f"ev{j}", "Paucius") for j in range(12)]           # 1 cell only
    return rows

# the reviewed name table (TASK-008): labels and aliases ride along as the
# optional third element; Ghostus is deliberately absent (unlisted genus).
TAXON_NAMES = """# reviewed fixture
genus,label_en,label_ja,aliases
Allowus,allowus,アロウス,al|アロウス属
Paucius,,
Rarus,,
Evencus,,
Matsutakus,,
"""

def slice_map(doc):
    return {(s[0], s[1]): {c[0]: (c[1], c[2]) for c in s[2]} for s in doc["slices"]}


class MeshResearch(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp()
        cls.islands = os.path.join(cls.tmp, "islands.csv")
        with open(cls.islands, "w") as f:
            f.write(ISLANDS_CSV)
        cls.refs = ["--mask", os.path.join(FIX, "taxon_mask.csv"),
                    "--coverage", write_coverage(cls.tmp),
                    "--exceptions", os.path.join(FIX, "match_exceptions.csv"),
                    "--redlist", os.path.join(FIX, "redlist.json")]
        cls.names = os.path.join(cls.tmp, "taxon_names.csv")
        with open(cls.names, "w") as f:
            f.write(TAXON_NAMES)
        cls.db = os.path.join(cls.tmp, "research.duckdb")
        c = duckdb.connect(cls.db)
        c.execute(f"create table jp({COLS})")
        c.executemany("insert into jp values (" + ",".join(["?"] * 22) + ")", fixture_rows())
        c.close()
        cls.out = cls.build()
        with open(cls.out, "rb") as f:
            cls.raw = f.read()
        cls.doc = json.loads(cls.raw)
        cls.sm = slice_map(cls.doc)

    @classmethod
    def build(cls, *extra, out=None, db=None):
        path = out or os.path.join(cls.tmp, "mesh-research.json")
        r = run_builder("build_mesh_research.py", "--db", db or cls.db, "--out", path,
                        "--islands", cls.islands, "--taxon-names", cls.names,
                        *cls.refs, *extra)
        assert r.returncode == 0, r.stdout + r.stderr
        cls.last_run = r
        return path

    def test_shape_and_slice_ordering(self):
        self.assertEqual(set(self.doc), {"schema_version", "snapshot", "slices", "genera"})
        self.assertEqual((self.doc["schema_version"], self.doc["snapshot"]), (1, ""))
        order = [(s[0], s[1]) for s in self.doc["slices"]]
        self.assertEqual(order, sorted(order, key=lambda mk: (mk[0], ("all", "m", "l", "o").index(mk[1]))))
        for s in self.doc["slices"]:
            keys = [c[0] for c in s[2]]
            self.assertEqual(keys, sorted(keys, key=lambda k: tuple(int(x) for x in k.split(":"))))
        self.assertIn("dedupe key gbifID skipped 3 duplicate source records", self.last_run.stdout)

    def test_joint_month_kind_aggregation(self):
        self.assertEqual(self.sm[(3, "m")], {A: (0, 1)})       # 12 species + 5 genus-rank, share 5/17 -> 20-<40%
        self.assertEqual(self.sm[(3, "all")], {A: (0, 1)})
        self.assertNotIn((3, "l"), self.sm)                    # kind l has no supported cell: slice withheld
        self.assertNotIn((0, "l"), self.sm)

    def test_one_record_counts_in_its_month_and_month0(self):
        self.assertEqual(self.sm[(9, "all")], {V: (0, None)})  # 10 records: band 0
        self.assertEqual(self.sm[(10, "all")], {V: (0, None)})  # 15 records: band 0
        self.assertEqual(self.sm[(0, "all")][V], (1, None))    # together 25: band 1

    def test_duplicate_source_records_count_once(self):
        everywhere = {k for s in self.sm.values() for k in s}
        self.assertNotIn(W, everywhere)                        # 9 after dedupe (11 raw): below support

    def test_support_thresholds_from_both_sides(self):
        everywhere = {k for s in self.sm.values() for k in s}
        self.assertNotIn(B, everywhere)                        # 9 records: withheld
        self.assertNotIn(C, everywhere)                        # 10 records but 2 events: withheld
        self.assertEqual(self.sm[(5, "all")], {D: (0, None)})  # 10 records / 3 events: ships
        self.assertNotIn(F, everywhere)                        # 10/3 but one flagged record: 9 eligible

    def test_share_sides_need_five_each(self):
        expect = dict(zip(S_KEYS, [(1, 1), (1, 0), (1, 4), (0, None), (0, 2)]))
        self.assertEqual(self.sm[(4, "all")], expect)          # 4/6 withholds share; 5/5..20/5 publish bands

    def test_count_band_boundaries(self):
        self.assertEqual(self.sm[(2, "all")], dict(zip(E_KEYS, [(0, None), (1, None), (1, None), (2, None),
                                                                (2, None), (3, None), (3, None), (4, None)])))

    def test_missing_month_lands_in_month0_only(self):
        self.assertEqual(self.sm[(0, "all")][MU], (0, None))
        for (m, k), cells in self.sm.items():
            if m >= 1:
                self.assertNotIn(MU, cells)

    def test_month0_keeps_cells_with_few_published_months(self):
        self.assertIn(T1, self.sm[(7, "all")])                 # the supported month ships
        self.assertNotIn((8, "all"), self.sm)                  # nothing supported in month 8
        self.assertIn(T1, self.sm[(0, "all")])                 # one published month leaves the remainder
        self.assertIn(T1, self.sm[(0, "m")])                   # unboundable: month 0 keeps the cell
        # the three genus-search fixture cells (Allowus/Rarus/Evencus/Matsutakus rows
        # at 26.0/26.1/26.25) are public records now that masking is species-only,
        # so they join month-0 'all' alongside the slice fixtures
        self.assertEqual(set(self.sm[(0, "all")]),
                         {A, D, T1, MU, V, PAUCIUS_CELL, "360:1030", "361:1030", "363:1030", *E_KEYS, *S_KEYS})

    def test_genera_gates_from_both_sides(self):
        self.assertEqual(self.doc["genera"], [["Allowus", ["okisle"],
                                               ["allowus", "アロウス", "al", "アロウス属"]],
                                              ["Matsutakus", ["t4528"], []]])  # per-area event gate keeps only the tile with >=3 events
        self.assertIn("genera dropped: {'support': 3}", self.last_run.stdout)

    def test_deterministic_bytes(self):
        with open(self.build(), "rb") as f:
            self.assertEqual(self.raw, f.read())

    def test_previous_file_suppresses_moved_bands(self):
        prev = json.loads(self.raw)
        for s in prev["slices"]:                               # pretend D's (5,'all') band moved; T1's did not
            if s[0] == 5 and s[1] == "all":
                s[2] = [[c[0], 1, c[2]] for c in s[2]]
        ppath = os.path.join(self.tmp, "prev.json")
        with open(ppath, "w") as f:
            json.dump(prev, f)
        with open(self.build("--previous", ppath)) as f:
            sm = slice_map(json.load(f))
        self.assertNotIn(D, sm[(5, "all")])                    # band moved vs the previous file: withheld
        self.assertIn(D, sm[(5, "m")])                         # kind slices keep it
        self.assertIn(D, sm[(0, "all")])                       # D publishes in a single month only, so even
                                                              # after the drop the remainder is unboundable
        self.assertIn(T1, sm[(7, "all")])                      # unchanged band: still ships
        self.assertIn(A, sm[(3, "all")])

    def test_absent_or_unreviewed_name_table_ships_empty_genera_with_warning(self):
        for name, text in (("absent", None), ("seed", "# UNREVIEWED SEED\ngenus,label_en,label_ja,aliases\n")):
            with self.subTest(names=name):
                table = os.path.join(self.tmp, f"names-{name}.csv")
                if text is not None:
                    with open(table, "w") as f:
                        f.write(text)
                out = os.path.join(self.tmp, f"research-{name}.json")
                r = run_builder("build_mesh_research.py", "--db", self.db, "--out", out,
                                "--islands", self.islands, "--taxon-names", table, *self.refs)
                self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
                self.assertIn("genus search disabled", r.stderr)
                doc = json.load(open(out))
                self.assertEqual(doc["genera"], [])
                self.assertTrue(doc["slices"])                 # slices still build (§6: the file stays legal)


class OccurrenceIDDedupe(unittest.TestCase):
    """A table without gbifID deduplicates on occurrenceID instead (documented choice)."""

    def test_occurrence_id_dedupe(self):
        tmp = tempfile.mkdtemp()
        db = os.path.join(tmp, "occ.duckdb")
        c = duckdb.connect(db)
        c.execute(f"create table jp({COLS_NO_GBIF})")
        rows = [list(rec(f"N{i}", 26.1, 127.7, 3, f"e{i % 3 + 1}")[1:]) for i in range(9)]   # no gbifID field
        rows[0][0] = "O1"                                      # first row carries occurrenceID O1...
        dup = list(rows[0])                                    # ...and an exact repeat of it arrives too
        c.executemany("insert into jp values (" + ",".join(["?"] * 21) + ")", [*rows, dup])
        c.close()
        out = os.path.join(tmp, "research.json")
        r = run_builder("build_mesh_research.py", "--db", db, "--out", out,
                        "--islands", os.path.join(FIX, "islands.csv"),
                        "--taxon-names", os.path.join(tmp, "none.csv"),
                        "--mask", os.path.join(FIX, "taxon_mask.csv"),
                        "--coverage", write_coverage(tmp),
                        "--exceptions", os.path.join(FIX, "match_exceptions.csv"),
                        "--redlist", os.path.join(FIX, "redlist.json"))
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("dedupe key occurrenceID skipped 1 duplicate source records", r.stdout)
        with open(out) as f:
            doc = json.load(f)
        self.assertEqual(doc["slices"], [])                    # 9 after dedupe: no supported cell anywhere


class SuppressionThreshold(unittest.TestCase):
    """TASK-009: month-0 suppression fires on the isolation threshold, not on any
    omitted month. A cell is withheld from month 0 of its kind only when >= 10 of
    the 12 single-month slices publish it (<= 2 unknown months against five-wide
    count bands is boundable); sparser cells ship, uniformly across kinds."""

    def test_ten_of_twelve_withholds_nine_ships(self):
        P11, P10, P9, SP = "366:1030", "367:1030", "369:1030", "370:1030"
        tmp = tempfile.mkdtemp()
        db = os.path.join(tmp, "supp.duckdb")
        c = duckdb.connect(db)
        c.execute(f"create table jp({COLS})")
        rows = []
        for lat, months in ((30.55, range(1, 12)),    # 11 of 12 published
                            (30.65, range(1, 11)),    # exactly 10: the threshold itself
                            (30.75, range(1, 10))):   # 9 of 12
            rows += [rec(f"P{int(lat * 100)}-{m}-{i}", lat, 128.76, m, f"e{i % 3}")
                     for m in months for i in range(10)]
        rows += [rec(f"SP-{i}", 30.85, 128.76, 4, f"e{i % 3}") for i in range(10)]   # one supported month...
        rows += [rec(f"SX-{i}", 30.85, 128.76, 5, f"e{i % 3}") for i in range(9)]    # ...one below support
        c.executemany("insert into jp values (" + ",".join(["?"] * 22) + ")", rows)
        c.close()
        out = os.path.join(tmp, "research.json")
        r = run_builder("build_mesh_research.py", "--db", db, "--out", out,
                        "--islands", os.path.join(FIX, "islands.csv"),
                        "--taxon-names", os.path.join(tmp, "none.csv"),
                        "--mask", os.path.join(FIX, "taxon_mask.csv"),
                        "--coverage", write_coverage(tmp),
                        "--exceptions", os.path.join(FIX, "match_exceptions.csv"),
                        "--redlist", os.path.join(FIX, "redlist.json"))
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("suppression: 4 cell(s) dropped from month 0", r.stdout)
        with open(out) as f:
            sm = slice_map(json.load(f))
        self.assertNotIn(P11, sm[(0, "all")])        # 11 published months: withheld
        self.assertNotIn(P11, sm[(0, "m")])          # the rule is uniform across kinds
        self.assertNotIn(P10, sm[(0, "all")])        # exactly 10: the threshold withholds
        self.assertNotIn(P10, sm[(0, "m")])
        self.assertIn(P9, sm[(0, "all")])            # 9 published months: three unknown defeat differencing
        self.assertIn(P9, sm[(0, "m")])
        self.assertIn(SP, sm[(0, "all")])            # sparse cell ships despite its below-support month
        self.assertIn(SP, sm[(0, "m")])
        self.assertEqual(set(sm[(4, "all")]), {P11, P10, P9, SP})   # single months publish untouched
        self.assertEqual(set(sm[(5, "all")]), {P11, P10, P9})       # SP's month 5 stays below support


class ScaledBudget(unittest.TestCase):
    """§8/§6 budget: a 2,763-cell universe at §1-like support rates stays <= 60 KiB
    gzip-9 with the genus names element present (TASK-008)."""

    def test_gzip_budget_on_scaled_fixture(self):
        tmp = tempfile.mkdtemp()
        db = os.path.join(tmp, "scaled.duckdb")
        header = [c.split()[0] for c in COLS.split(", ")]
        tsv = os.path.join(tmp, "scaled.tsv")
        kinds = ["Agaricomycetes", "Lecanoromycetes", "Fictomycetes"]
        gid = 0
        with open(tsv, "w", newline="\n") as f:
            f.write("\t".join(header) + "\n")

            def one(lat, lon, month, ev, rank="SPECIES"):
                nonlocal gid
                gid += 1
                sp = "Prosus lautus" if rank == "SPECIES" else ""
                return (f"g{gid}\t\tk{gid}\t{sp}\tProsus\tFictaceae\t{rank}\tHUMAN_OBSERVATION\t"
                        f"{kinds[0]}\tBasidiomycota\t{lat!r}\t{lon!r}\t{month}\t2020\t\tOkinawa\tdx\t{ev}\t\t\t\t\n")

            for i in range(2763):                              # the live dashboard's cell count (plan §1)
                row_, col_ = 240 + i % 312, 976 + (i // 312) % 256
                lat, lon = (row_ + 0.3) / 12, (col_ + 0.4) / 8
                kinds.append(kinds.pop(0))                     # rotate m -> l -> o per cell
                mon = i % 12 + 1                               # seasonality-shaped: records in one month,
                if i % 3 == 0:                                 # else every small month is withheld and the
                    f.writelines(one(lat, lon, mon, f"e{(gid // 4) % 7}") for _ in range(10))
                    f.writelines(one(lat, lon, mon, f"e{(gid // 4) % 7}", rank="GENUS") for _ in range(10))
                else:                                          # complementary pass drops month 0 entirely
                    f.writelines(one(lat, lon, mon, f"e{(gid // 4) % 7}") for _ in range(12 + i % 19))
        c = duckdb.connect(db)
        c.execute(f"create table jp as select * from read_csv('{tsv}', delim='\\t', header=true, "
                  f"quote='', all_varchar=true)")
        c.close()
        names = os.path.join(tmp, "taxon_names.csv")           # TASK-008: names present in the payload
        with open(names, "w") as f:
            f.write("genus,label_en,label_ja,aliases\nProsus,prosus,プロスス,pro\n")
        out = os.path.join(tmp, "mesh-research.json")
        r = run_builder("build_mesh_research.py", "--db", db, "--out", out,
                        "--islands", os.path.join(FIX, "islands.csv"),
                        "--taxon-names", names,
                        "--mask", os.path.join(FIX, "taxon_mask.csv"),
                        "--coverage", write_coverage(tmp),
                        "--exceptions", os.path.join(FIX, "match_exceptions.csv"),
                        "--redlist", os.path.join(FIX, "redlist.json"))
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        with open(out, "rb") as f:
            raw = f.read()
        doc = json.loads(raw)
        entries = sum(len(s[2]) for s in doc["slices"])
        month0 = next(s for s in doc["slices"] if s[0] == 0 and s[1] == "all")
        self.assertEqual(len(month0[2]), 2763)                 # no withheld month anywhere: nothing suppressed
        biggest = max(len(s[2]) for s in doc["slices"] if s[0] >= 1 and s[1] == "all")
        self.assertGreaterEqual(len(month0[2]), biggest)       # TASK-009: month 0 never sparser than a month
        self.assertGreater(entries, 9000)                      # the fixture genuinely stresses the budget
        self.assertEqual(len(doc["genera"]), 1)                # the listed genus ships with its names
        genus, areas, namelist = doc["genera"][0]
        self.assertEqual((genus, namelist), ("Prosus", ["prosus", "プロスス", "pro"]))
        self.assertTrue(areas)
        gz = len(gzip.compress(raw, 9))
        self.assertLessEqual(gz, 60 * KIB, f"{gz:,} gzip-9 bytes over {entries:,} cell entries")


class SeededTaxonNames(unittest.TestCase):
    """TASK-008: data/reference/taxon_names.csv — David's reviewed bilingual name
    table. Every name that can enter the payload must be a genus-level coarse
    term: aliases single-token (a binomial always carries a space), no
    binomial-shaped label, and nothing the real protected-taxon gate masks."""

    @classmethod
    def setUpClass(cls):
        with open(os.path.join(REPO, "data", "reference", "taxon_names.csv"),
                  newline="", encoding="utf-8") as f:
            cls.rows = list(csv.DictReader(ln for ln in f if not ln.lstrip().startswith("#")))
        cls.gate = load_gate(mask=os.path.join(REPO, "data", "reference", "taxon_mask.csv"),
                             coverage=os.path.join(REPO, "data", "reference", "redlist_coverage.csv"),
                             exceptions=os.path.join(REPO, "data", "reference", "match_exceptions.csv"),
                             redlist=os.path.join(REPO, "data", "raw", "redlist", "redlist_fungi_jp.json"))

    @staticmethod
    def names_of(row):
        aliases = [a.strip() for a in (row.get("aliases") or "").split("|") if a.strip()]
        return [(row.get(k) or "").strip() for k in ("label_en", "label_ja")], aliases

    def test_table_parses_with_unique_latin_genera(self):
        self.assertGreater(len(self.rows), 20)                  # a real curated list, not a stub
        genera = [(r.get("genus") or "").strip() for r in self.rows]
        for g in genera:
            self.assertRegex(g, r"^[A-Z][a-z]+$", g)
        self.assertEqual(len({g.lower() for g in genera}), len(genera))   # no duplicate genus

    def test_names_are_genus_level_and_never_protected(self):
        for row in self.rows:
            labels, aliases = self.names_of(row)
            for alias in aliases:                               # a binomial always has a space
                self.assertNotRegex(alias, r"\s", alias)
            for name in [*labels, *aliases]:
                self.assertNotRegex(name, r"^[A-Z][a-z]+ [a-z]+$", name)  # Linnaean shape
                self.assertFalse(self.gate.classify(species=name, taxon_rank="SPECIES"),
                                 f"{name!r} is a protected name and must not ship")
            for alias in aliases:                               # never alias a masked genus word
                self.assertFalse(self.gate.classify(genus=alias, taxon_rank="GENUS"), alias)


class EndToEndBundle(unittest.TestCase):
    """The full fixture bundle, built exactly the way publish-dashboard.yml builds it,
    with mesh-research.json present: the publication gate must say publishable."""

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp()
        raw = os.path.join(cls.tmp, "raw")
        os.makedirs(raw)

        def tsv(name, header, rows):
            with open(os.path.join(raw, name), "w", newline="\n") as f:
                f.write("\t".join(header) + "\n")
                for r in rows:
                    f.write("\t".join("" if v is None else str(v) for v in r) + "\n")

        jp_header = ["gbifID", "speciesKey", "species", "genus", "family", "taxonRank", "basisOfRecord",
                     "class", "phylum", "decimalLatitude", "decimalLongitude", "month", "year",
                     "locality", "stateProvince", "datasetKey", "coordinateUncertaintyInMeters",
                     "coordinatePrecision", "issue", "eventID", "recordedBy"]
        # rec() field order -> the jp TSV column order (occurrenceID dropped, events appended)
        to_tsv = (0, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15, 16, 19, 20, 21, 17, 18)
        jp_rows = [tuple(r[i] for i in to_tsv)
                   for r in (rec(f"R{i}", 26.1, 127.7, 3 if i < 12 else 5, f"e{i % 4 + 1}")
                             for i in range(16))]
        # TASK-008: 12 genus-rank Russula records over 3 okisle cells / 12 events in month 6
        # (below cell support, so no slice changes) — the repo's seeded real name table
        # ships the genus with its bilingual names through the whole publish path.
        jp_rows += [tuple(rec(f"GR{i}", 26.1 + i % 3 * 0.1, 127.7, 6, f"ge{i}",
                              rank="GENUS", genus="Russula")[i_2] for i_2 in to_tsv)
                    for i in range(12)]
        tsv("jp.tsv", jp_header, jp_rows)
        tsv("nb.tsv", ["species", "phylum", "order", "family", "genus", "class", "numberOfOccurrences",
                       "iucnRedListCategory", "taxonRank", "taxonomicStatus", "speciesKey"], NB_ROWS)
        tsv("mo.tsv", ["speciesKey", "countryCode"], MO_ROWS)
        tsv("dwca.tsv", ["gbifID", "habitat", "associatedTaxa", "elevation",
                         "dataGeneralizations", "informationWithheld"], [])
        meta = os.path.join(raw, "meta.json")
        with open(meta, "w") as f:
            json.dump({"key": "dx", "doi": "10.9999/fake-doi", "totalRecords": 28,
                       "created": "2026-11-21T00:00:00Z"}, f)
        cls.islands = os.path.join(cls.tmp, "islands.csv")
        with open(cls.islands, "w") as f:
            f.write(ISLANDS_CSV)
        cls.refs = ["--mask", os.path.join(FIX, "taxon_mask.csv"),
                    "--coverage", write_coverage(cls.tmp),
                    "--exceptions", os.path.join(FIX, "match_exceptions.csv"),
                    "--redlist", os.path.join(FIX, "redlist.json")]
        cls.db = os.path.join(cls.tmp, "scratch.duckdb")
        cls.staging = os.path.join(cls.tmp, "staging")
        os.makedirs(cls.staging)
        for script, *args in (
                ("build_db.py", "--japan", os.path.join(raw, "jp.tsv"),
                 "--neighbours", os.path.join(raw, "nb.tsv"), "--mo", os.path.join(raw, "mo.tsv"),
                 "--japan-dwca", os.path.join(raw, "dwca.tsv"), "--db", cls.db),
                ("build_dashboard_data.py", "--db", cls.db,
                 "--out", os.path.join(cls.staging, "dashboard.json"), "--meta", meta, *cls.refs),
                ("build_areas.py", "--db", cls.db, "--out", os.path.join(cls.staging, "areas"),
                 "--islands", cls.islands, *cls.refs),
                ("build_mesh_research.py", "--db", cls.db,
                 "--out", os.path.join(cls.staging, "mesh-research.json"),
                 "--islands", cls.islands, *cls.refs),
                ("snapshot.py", cls.staging)):
            r = run_builder(script, *args)
            assert r.returncode == 0, script + "\n" + r.stdout + r.stderr
        with open(os.path.join(cls.staging, "mesh-research.json")) as f:
            cls.research = json.load(f)

    def test_fixture_bundle_with_research_validates(self):
        r = subprocess.run([sys.executable, os.path.join(REPO, "pipeline", "validate_dashboard_publish.py"),
                            self.staging, *self.refs], capture_output=True, text=True, cwd=REPO)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("publishable", r.stdout)
        self.assertIn("7 files", r.stdout)                     # 6 occurrence files + research joined the bundle
        digest = bundle_digest(self.staging)
        self.assertEqual(self.research["snapshot"], digest)    # and the digest covers it
        sm = slice_map(self.research)
        self.assertEqual(sm[(3, "all")], {A: (0, None)})       # 12 records / 4 events: a real shipped slice
        # the repo's seeded real name table (data/reference/taxon_names.csv) ships Russula
        # with its reviewed bilingual names; only listed genera can appear, mask winning
        self.assertEqual(self.research["genera"],
                         [["Russula", ["okisle"], ["brittlegills", "ベニタケ属"]]])

    def test_workflow_runs_research_before_the_stamp_outside_geography_only(self):
        with open(os.path.join(REPO, ".github", "workflows", "publish-dashboard.yml")) as f:
            text = f.read()
        at = [text.index(m) for m in ("build_areas.py --out staging", "build_mesh_research.py --out staging",
                                      "snapshot.py staging", "validate_dashboard_publish.py staging")]
        self.assertEqual(at, sorted(at), "areas -> research -> stamp -> validate order broken")
        step = text[text.index("- name: Build research payload"):text.index("- name: Validate staging bundle")]
        self.assertIn("inputs.geography_only != true", step)   # kept out of the geography-only run


if __name__ == "__main__":
    unittest.main()
