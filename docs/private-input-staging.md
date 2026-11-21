# Private input staging (Phase 0A)

Raw GBIF / Mushroom Observer / Red List downloads are occurrence data with exact coordinates.
As of 21 November 2026 they were uploaded as Releases on the **public** ShroomHunt repo. This
change stops that: new raw inputs go to a configured **private** staging repo, and the public
publish path is paused until the v1 output contract (Phase 1C) validates what leaves the
pipeline. Spec: `docs/map-improvement-plan.md` §5 row 0A and §7.

## One-time setup (David)

1. Create a **private** repository, e.g. `davidklan-png/ShroomHunt-staging`. No code, no
   Actions — it only holds data Releases.
2. Create a fine-grained personal access token: **Repository access** limited to that one repo,
   **Contents: read and write**. Nothing else.
3. ShroomHunt repo → Settings → Secrets and variables → Actions:
   - variable `STAGING_REPO` = `owner/name` of the private repo
   - secret `STAGING_TOKEN` = the token from step 2

Until both exist, `fetch-data.yml` and `publish-dashboard.yml` fail closed by design.

## What changed

- **`.github/workflows/fetch-data.yml`** — the old "Publish as a release" step created a
  release on public ShroomHunt with the workflow's own token. It is now "Stage as a private
  release": it requires `STAGING_REPO`/`STAGING_TOKEN`, verifies via
  `pipeline/fetch_latest_release_assets.py --verify-destination` that the destination exists,
  accepts the token **and** is private, and only then runs
  `gh release create --repo "$STAGING_REPO"`. The job's `permissions` dropped to
  `contents: read`, so the built-in `GITHUB_TOKEN` cannot create public releases even by
  accident. Tag, notes and per-download DOI links are unchanged. Historical releases on public
  ShroomHunt are left untouched — there is no delete path anywhere.
- **`pipeline/fetch_latest_release_assets.py`** — source repo and token are now parameters
  (`--repo`/`--token` or `STAGING_REPO`/`STAGING_TOKEN`, falling back to `GH_TOKEN`); it no
  longer assumes this repo's own releases. Missing config, unreachable repo (404/403/401) or
  incomplete inputs exit with a message before anything is written. The "newest release with
  complete inputs" selection lives in `select_inputs()` (pure, unit-tested), and the
  private-destination check in `verify_destination()`.
- **`.github/workflows/publish-dashboard.yml`** — downloads inputs from the staging repo via
  the script above. The "Open pull request on Bio_HP" step is gated behind the
  `enable_public_pr` workflow input (**default false**); without it the build still runs and a
  step summary explains the pause. On the automatic `workflow_run` trigger the input is never
  set, so a data PR can only be opened by an explicit manual run with the box ticked — and
  should only be once Phase 1C's `validate_dashboard_publish.py` gates the output.
- **`tests/test_fetch_latest_release_assets.py`** — unittest suite (run by
  `handoff/verify.sh`): complete/incomplete/empty release listings, newest-release-per-prefix
  selection, prefix disambiguation (`japan_` vs `japan_dwca_`), and the fail-closed matrix —
  private ok, public rejected, 404/bad-token rejected, missing repo/token config rejected.
  The gh subprocess boundary is mocked; tests never touch the network.

## Open decision: already-public raw releases

Releases made before this change remain public, notably `data-20261121-0204` (28.7 MB East
Asia raw archive) and any earlier data tags. Removing them is **David's call, not yet made**:
deleting a release hides the asset but forks, clones and caches may retain copies
(plan §7 "document the window of exposure and David's decision about withdrawal"). When
decided: delete via the GitHub UI or `gh release delete` — this repo deliberately ships no
deletion automation.
