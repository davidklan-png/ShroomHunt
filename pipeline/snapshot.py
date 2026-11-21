#!/usr/bin/env python3
"""One SHA-256 bundle snapshot for the public occurrence files (§6, TASK-006).

dashboard.json, areas/index.json, areas/<id>.json (sorted by id) and the
optional mesh-research.json form one atomic bundle: the snapshot is the
SHA-256 of their canonical unstamped JSON concatenated in that order.
Research is included whenever present — changing optional data must
invalidate a stale join. Reference geography (japan-outline.json) versions
independently and is never part of the digest.

Canonical means json.dumps(sort_keys=True, ensure_ascii=False,
separators=(",", ":")) with the top-level snapshot field emptied, so the
digest depends only on content, never on incidental byte layout. Both
builders write through write_json(); the stamper runs last over the staging
directory (build order: dashboard -> areas -> python3 pipeline/snapshot.py
<staging-dir>).
"""
import argparse, hashlib, json, os, sys

RESEARCH = "mesh-research.json"


def canonical(obj):
    """The one deterministic serialization every published file uses (LF-terminated by write_json)."""
    return json.dumps(obj, sort_keys=True, ensure_ascii=False, separators=(",", ":"))


def write_json(path, obj):
    """Write obj as canonical JSON with an LF terminator."""
    with open(path, "w", newline="\n", encoding="utf-8") as f:
        f.write(canonical(obj) + "\n")


def bundle_paths(staging):
    """Bundle member paths in §6 order — each optional except when the caller needs it."""
    areas = os.path.join(staging, "areas")
    paths = [p for p in (os.path.join(staging, "dashboard.json"),
                         os.path.join(areas, "index.json")) if os.path.exists(p)]
    if os.path.isdir(areas):
        paths += sorted(os.path.join(areas, f) for f in os.listdir(areas)
                        if f.endswith(".json") and f != "index.json")
    research = os.path.join(staging, RESEARCH)
    if os.path.exists(research):
        paths.append(research)
    return paths


def _load(path):
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def bundle_digest(staging):
    """SHA-256 of the concatenated canonical unstamped bundle members."""
    h = hashlib.sha256()
    for p in bundle_paths(staging):
        obj = _load(p)
        obj["snapshot"] = ""          # top-level only: the unstamped form
        h.update(canonical(obj).encode("utf-8"))
    return h.hexdigest()


def stamp(staging):
    """Compute the bundle digest and write it into every member's snapshot field."""
    paths = bundle_paths(staging)
    if not paths:
        raise SystemExit(f"no bundle members found to stamp under {staging}")
    digest = bundle_digest(staging)
    for p in paths:
        obj = _load(p)
        obj["snapshot"] = digest
        write_json(p, obj)
    return digest, paths


def main(argv=None):
    ap = argparse.ArgumentParser(description="Stamp the bundle snapshot into every occurrence file "
                                             "(run last over the staging dir, after both builders).")
    ap.add_argument("staging", help="staging directory holding dashboard.json and areas/")
    args = ap.parse_args(argv)
    digest, paths = stamp(args.staging)
    print(f"stamped {len(paths)} files in {args.staging}: snapshot {digest}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
