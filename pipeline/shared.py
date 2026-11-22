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
