#!/usr/bin/env python3
"""Request research-grade GBIF downloads for ShroomHunt (Japan fungal diversity).

  japan       Every fungus record GBIF holds for Japan, unfiltered: all ranks (incl. genus-only and
              'sp.' records), specimens, observations, DNA-derived records, with collector, voucher,
              coordinates and uncertainty. Format SIMPLE_CSV.
  mushroomobserver  Mushroom Observer's complete dataset as published to GBIF (DOI 10.15468/wwcmna), worldwide.
              Used to cross-reference: MO records are also inside 'japan', tagged by datasetKey.
  japan_dwca  Same as 'japan' as a Darwin Core Archive (adds habitat, substrate, remarks).
  eastasia    Neighbouring East Asia records with coordinates (aggregated locally into species x cell x month).
  neighbours  Species list of fungi recorded in mainland East Asia, Korea, Taiwan and the Russian Far East, to flag
              species known next door but never recorded in Japan. Format SPECIES_LIST.

Needs a free GBIF account. From the ShroomHunt folder:
    export GBIF_USER=yourname GBIF_PWD=yourpassword GBIF_EMAIL=you@example.com
    python3 pipeline/gbif_request.py            # both
    python3 pipeline/gbif_request.py japan      # one
    python3 pipeline/gbif_request.py --resume <download-key>
Each download gets a DOI: cite it in anything you publish.
"""
import base64, json, os, sys, time, urllib.request, urllib.error

FUNGI = {"type": "equals", "key": "TAXON_KEY", "value": "5"}
REQUESTS = {
    "japan": ("SIMPLE_CSV", {"type": "and", "predicates": [FUNGI, {"type": "equals", "key": "COUNTRY", "value": "JP"}]}),
    "mushroomobserver": ("SIMPLE_CSV", {"type": "equals", "key": "DATASET_KEY",
                         "value": "d714382d-5890-4234-ae81-696eeb53658a"}),
    # East Asia box (lon 100-155E, lat 18-56N) minus Japan: China east of 100E, Koreas, Taiwan, Russian Far East
    # (Primorye, Amur, Khabarovsk, Sakhalin, Kurils). A country filter on RU pulled in European Russia.
    # Same records as 'japan' but as a Darwin Core Archive, which carries habitat, substrate and remarks.
    "japan_dwca": ("DWCA", {"type": "and", "predicates": [FUNGI, {"type": "equals", "key": "COUNTRY", "value": "JP"}]}),
    # Neighbouring East Asia records with coordinates (same area as 'neighbours'), aggregated locally by
    # build_db.py into species x 1-degree cell x month. Feeds "could be found here". (A GBIF SQL download
    # returned 0 rows on 2026-11-21, so the predicate route is used.)
    "eastasia": ("SIMPLE_CSV", {"type": "and", "predicates": [FUNGI,
                 {"type": "within", "geometry": "POLYGON((100 18,155 18,155 56,100 56,100 18))"},
                 {"type": "not", "predicate": {"type": "equals", "key": "COUNTRY", "value": "JP"}},
                 {"type": "equals", "key": "HAS_COORDINATE", "value": "true"},
                 {"type": "equals", "key": "HAS_GEOSPATIAL_ISSUE", "value": "false"}]}),
    "neighbours": ("SPECIES_LIST", {"type": "and", "predicates": [FUNGI,
                   {"type": "within", "geometry": "POLYGON((100 18,155 18,155 56,100 56,100 18))"},
                   {"type": "not", "predicate": {"type": "equals", "key": "COUNTRY", "value": "JP"}}]}),
}
API = "https://api.gbif.org/v1/occurrence/download"

def call(url, data=None, auth=None):
    req = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json"})
    if auth: req.add_header("Authorization", "Basic " + base64.b64encode(auth.encode()).decode())
    with urllib.request.urlopen(req, timeout=300) as r: return r.read()

def submit(name, u, p, e):
    fmt, pred = REQUESTS[name]
    req = {"creator": u, "notificationAddresses": [e], "sendNotification": True, "format": fmt}
    if fmt == "SQL_TSV_ZIP": req["sql"] = pred
    else: req["predicate"] = pred
    body = json.dumps(req).encode()
    try: key = call(API + "/request", body, f"{u}:{p}").decode().strip()
    except urllib.error.HTTPError as err:
        sys.exit(f"GBIF refused '{name}' ({err.code}): {err.read().decode()[:800]}")
    print(f"[{name}] submitted, key {key} (resume with --resume {key})"); return key

def wait_and_fetch(key, label):
    while True:
        st = json.loads(call(f"{API}/{key}"))
        print(time.strftime("%H:%M:%S"), f"[{label}]", st.get("status"), f"{st.get('totalRecords') or ''}")
        if st.get("status") == "SUCCEEDED": break
        if st.get("status") in ("FAILED", "KILLED", "CANCELLED"): sys.exit("GBIF download failed: " + json.dumps(st)[:800])
        time.sleep(60)
    os.makedirs("data/raw/gbif", exist_ok=True)
    out = f"data/raw/gbif/{label}_{key}.zip"
    with open(out, "wb") as f: f.write(call(st["downloadLink"]))
    meta = {k: st.get(k) for k in ("key", "doi", "created", "totalRecords", "license")}
    meta["request"] = st.get("request")
    json.dump(meta, open(out[:-4] + ".json", "w"), indent=2)
    print(f"Saved {out} | cite as https://doi.org/{st.get('doi')}")

def main():
    a = sys.argv[1:]
    if a[:1] == ["--resume"]: return wait_and_fetch(a[1], "resumed")
    u, p, e = (os.environ.get(k) for k in ("GBIF_USER", "GBIF_PWD", "GBIF_EMAIL"))
    if not (u and p and e): sys.exit("Set GBIF_USER, GBIF_PWD and GBIF_EMAIL first (see top of this file).")
    names = a or ["japan", "neighbours", "mushroomobserver", "eastasia"]
    keys = {n: submit(n, u, p, e) for n in names}   # submit all first so GBIF processes them in parallel
    for n, k in keys.items(): wait_and_fetch(k, n)

if __name__ == "__main__": main()
