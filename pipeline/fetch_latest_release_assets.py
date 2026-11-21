#!/usr/bin/env python3
"""Download the newest release asset for each input the dashboard needs (japan, neighbours,
mushroomobserver, redlist) from the private staging repo's releases into data/raw/.
Source and credentials come from --repo/--token or $STAGING_REPO/$STAGING_TOKEN (fallback
$GH_TOKEN); a missing or inaccessible source fails closed before anything is written.
With --verify-destination, only check that the configured repo is reachable and private
(fetch-data.yml runs that before uploading raw data). See docs/private-input-staging.md.
Usage: python3 pipeline/fetch_latest_release_assets.py [--repo owner/name] [--token TOKEN]"""
import argparse, json, os, re, subprocess, sys, zipfile

# name pattern -> (folder, required). Exact patterns, because "japan_" would also match "japan_dwca_".
PREFIXES = {"japan_": ("data/raw/gbif", True), "neighbours_": ("data/raw/gbif", True),
            "mushroomobserver_": ("data/raw/gbif", True), "redlist_fungi_jp": ("data/raw/redlist", True),
            "japan_dwca_": ("data/raw/gbif", False), "eastasia_": ("data/raw/gbif", False)}

class StagingError(Exception):
    """Fail-closed problem with the source repo or its releases; the message is user-facing."""

def matches(name, p):
    return re.match(re.escape(p) + (r"\d" if p.endswith("_") else ""), name) is not None

def select_inputs(releases, prefixes=PREFIXES):
    """For each prefix, the assets of the newest release that carries one (releases newest first).
    Raises StagingError naming every required prefix that no release provides."""
    picked = {}                                                       # prefix -> assets of its newest release
    for p in prefixes:
        for rel in releases:
            assets = [a for a in rel["assets"] if matches(a["name"], p)]
            if assets:
                picked[p] = [dict(a, release=rel["tag_name"]) for a in assets]
                break
    missing = [p for p, (_, req) in prefixes.items() if req and p not in picked]
    if missing: raise StagingError(f"No release asset found for: {missing}")
    return picked

def gh(*args, token, binary=False):
    env = dict(os.environ, GH_TOKEN=token)
    out = subprocess.run(["gh", "api", *args], capture_output=True, env=env)
    if out.returncode:
        detail = [l for l in out.stderr.decode("utf-8", "replace").splitlines() if l.strip()]
        raise StagingError(f"gh api {' '.join(args)} failed: {detail[-1] if detail else 'exit ' + str(out.returncode)}")
    return out.stdout if binary else json.loads(out.stdout)

def verify_destination(repo, token):
    """Fail closed unless the staging repo answers to this token AND is private."""
    try:
        info = gh(f"repos/{repo}", token=token)
    except StagingError as e:
        raise StagingError(f"Staging repo {repo} is not accessible with this token "
                           f"(bad token, no access, or no such repo): {e}") from None
    if not info.get("private"):
        raise StagingError(f"Staging repo {repo} is public; refusing to touch raw occurrence data")
    return info

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("pos_repo", nargs="?", help="source repo owner/name (default: --repo, then $STAGING_REPO)")
    ap.add_argument("--repo", help="source repo owner/name (default: $STAGING_REPO)")
    ap.add_argument("--token", help="GitHub token (default: $STAGING_TOKEN, then $GH_TOKEN)")
    ap.add_argument("--verify-destination", action="store_true",
                    help="only check the repo is reachable and private, then exit (no download)")
    a = ap.parse_args()
    repo = a.repo or a.pos_repo or os.environ.get("STAGING_REPO")
    token = a.token or os.environ.get("STAGING_TOKEN") or os.environ.get("GH_TOKEN")
    if not repo: sys.exit("No source repo configured: pass --repo or set STAGING_REPO "
                          "(see docs/private-input-staging.md)")
    if not token: sys.exit("No token configured: pass --token or set STAGING_TOKEN/GH_TOKEN")
    try:
        if a.verify_destination:
            verify_destination(repo, token)
            print(f"{repo}: reachable and private")
            return
        releases = gh(f"repos/{repo}/releases?per_page=100", token=token)   # newest first
        picked = select_inputs(releases)
        for p, asset in ((p, a) for p, lst in picked.items() for a in lst):
            os.makedirs(PREFIXES[p][0], exist_ok=True)
            path = os.path.join(PREFIXES[p][0], asset["name"])
            open(path, "wb").write(gh("-H", "Accept: application/octet-stream",
                                      f"repos/{repo}/releases/assets/{asset['id']}", token=token, binary=True))
            if path.endswith(".zip"):
                with zipfile.ZipFile(path) as z:
                    members = [m for m in z.namelist() if p != "japan_dwca_" or m == "occurrence.txt"]   # DwC-A: occurrences only
                    z.extractall(os.path.dirname(path) + "/" + p.rstrip("_"), members=members)
            print(f"{asset['release']}: {asset['name']}")
    except StagingError as e:
        sys.exit(str(e))

if __name__ == "__main__":
    main()
