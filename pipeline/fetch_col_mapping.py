#!/usr/bin/env python3
"""Download the static GBIF-backbone -> COL XR crosswalk (TASK-018, spec §0b).

Source: https://download.checklistbank.org/col/gbif/gbif-col265.tsv.gz
(COL 26.5 eXtended Release, matched against the GBIF backbone of 2023-08-28;
~500 MB gzipped, one TSV row per backbone usage). No auth, no secrets.

The server sits behind a cookie "go-away" challenge: each request retries with
the accumulated cookie jar until a gzip body comes back, so the jar is kept on
disk and reused across runs. The response Last-Modified is stored in a sidecar
file next to the download; when it is unchanged the server answers 304 and the
download is skipped entirely.

Usage: python3 pipeline/fetch_col_mapping.py [--out data/raw/col] [--force]
"""
import argparse, gzip, http.cookiejar, os, sys, time, urllib.error, urllib.request

URL = "https://download.checklistbank.org/col/gbif/gbif-col265.tsv.gz"
FILE = "gbif-col265.tsv.gz"
RETRIES, BACKOFF = 6, 2.0


def fetch(out_dir, force=False):
    os.makedirs(out_dir, exist_ok=True)
    path = os.path.join(out_dir, FILE)
    sidecar, jar_path = path + ".last-modified", os.path.join(out_dir, "cookies.jar")
    jar = http.cookiejar.LWPCookieJar(jar_path)
    if os.path.exists(jar_path):
        jar.load(ignore_discard=True, ignore_expires=True)
    opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar))
    opener.addheaders = [("User-Agent", "ShroomHunt-pipeline/1.0 (COL XR re-keying)")]

    headers = {}
    if not force and os.path.exists(path) and os.path.exists(sidecar):
        headers["If-Modified-Since"] = open(sidecar).read().strip()
    req = urllib.request.Request(URL, headers=headers)
    for attempt in range(1, RETRIES + 1):
        try:
            with opener.open(req, timeout=120) as r:
                if r.status == 304:
                    print(f"{path}: unchanged (HTTP 304, Last-Modified "
                          f"{headers['If-Modified-Since']}); skipping download")
                    return path
                body = r.read(2)
                if body != b"\x1f\x8b":
                    raise OSError(f"challenge page (not gzip) on attempt {attempt}")
                rest = r.read()
                tmp = path + ".tmp"
                with open(tmp, "wb") as f:
                    f.write(body + rest)
                os.replace(tmp, path)
                jar.save(ignore_discard=True, ignore_expires=True)
                lm = r.headers.get("Last-Modified")
                if lm:
                    open(sidecar, "w").write(lm + "\n")
                print(f"{path}: downloaded {os.path.getsize(path):,} bytes "
                      f"(Last-Modified {lm or 'unknown'})")
                return path
        except urllib.error.HTTPError as e:
            if e.code == 304:                       # urllib raises instead of returning 304
                print(f"{path}: unchanged (HTTP 304, Last-Modified "
                      f"{headers['If-Modified-Since']}); skipping download")
                return path
            print(f"attempt {attempt}/{RETRIES} failed: {e}", file=sys.stderr)
            time.sleep(BACKOFF * attempt)
        except OSError as e:
            print(f"attempt {attempt}/{RETRIES} failed: {e}", file=sys.stderr)
            time.sleep(BACKOFF * attempt)
    sys.exit(f"could not download {URL} after {RETRIES} attempts")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="data/raw/col")
    ap.add_argument("--force", action="store_true", help="ignore the stored Last-Modified")
    a = ap.parse_args()
    fetch(a.out, a.force)
