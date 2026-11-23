"""Helpers shared by the area and research builders."""
# Kind of fungus, from taxonomy: 'm' = mushrooms and other fruiting bodies you can see in the field,
# 'l' = lichens, 'o' = microfungi, yeasts, moulds and DNA-only groups. Approximate by design.
MACRO_CLASSES = {"Agaricomycetes", "Pezizomycetes", "Dacrymycetes"}
MACRO_GENERA = {"Tremella", "Cordyceps", "Ophiocordyceps", "Tolypocladium", "Polycephalomyces", "Xylaria", "Daldinia",
                "Hypoxylon", "Annulohypoxylon", "Kretzschmaria", "Hypomyces", "Leotia", "Cudonia", "Bulgaria",
                "Chlorociboria", "Ascocoryne", "Geoglossum", "Trichoglossum", "Rhytisma"}
LICHEN_CLASSES = {"Lecanoromycetes", "Arthoniomycetes", "Lichinomycetes", "Coniocybomycetes"}
LICHEN_FAMILIES = {"Pyrenulaceae", "Trypetheliaceae", "Verrucariaceae", "Strigulaceae", "Monoblastiaceae",
                   "Arthopyreniaceae", "Collemataceae"}

def kind(cls, fam, genus):
    if cls in LICHEN_CLASSES or fam in LICHEN_FAMILIES: return "l"
    if cls in MACRO_CLASSES or genus in MACRO_GENERA: return "m"
    return "o"

def specimen_key(inst, cat):
    """Normalised (institutionCode, catalogNumber) — strip, casefold, collapse internal
    whitespace — or None when either side is empty: one physical specimen published by
    two datasets counts once (TASK-014); an empty catalogNumber identifies nothing."""
    def norm(v): return " ".join(str(v).split()).casefold() if v is not None else ""
    i, c = norm(inst), norm(cat)
    return (i, c) if i and c else None

def dedup_temp_jp(con):
    """Shadow jp with a cross-dataset specimen-deduped copy (TASK-016 idiom):
    keep each normalised (institutionCode, catalogNumber) pair's first record in
    gbifID order. Builders aggregating raw jp rows call this before their queries
    so their counts match the published dashboard record semantics. A jp without
    the naming columns skips dedup (fixtures); returns rows dropped."""
    cols = {r[0] for r in con.execute(
        "select column_name from information_schema.columns where table_name='jp'").fetchall()}
    if not {"gbifID", "institutionCode", "catalogNumber"} <= cols:
        return 0
    seen, drop = set(), []
    for gid, inst, cat in con.execute(
            "select gbifID, institutionCode, catalogNumber from jp order by gbifID nulls last").fetchall():
        pair = specimen_key(inst, cat)
        if pair is None:
            continue
        if pair in seen:
            if gid is not None:
                drop.append((gid,))
        else:
            seen.add(pair)
    if drop:
        con.execute("create temp table xdrop as select gbifID from jp limit 0")
        con.executemany("insert into xdrop values (?)", drop)
        con.execute("create temp table jp as select * from main.jp "
                    "where gbifID is null or gbifID not in (select gbifID from xdrop)")
    return len(drop)
