"""Tests for TASK-001: input-release selection and staging-repo fail-closed checks.
No network — the gh subprocess boundary is mocked throughout."""
import io, os, subprocess, sys, unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from pipeline.fetch_latest_release_assets import (PREFIXES, StagingError, main, matches,
                                                  select_inputs, verify_destination)

def rel(tag, *names):
    """A release listing entry: tag_name plus assets named by prefix."""
    return {"tag_name": tag, "assets": [{"id": f"{tag}/{n}", "name": n} for n in names]}

def gh_result(payload=None, returncode=0, stderr=b""):
    stdout = (payload if isinstance(payload, bytes) else
              ("" if payload is None else '{"private": %s}' % payload).encode())
    return subprocess.CompletedProcess(args=[], returncode=returncode, stdout=stdout, stderr=stderr)

ALL = [rel("data-2", "eastasia_00261120.csv", "japan_dwca_00261120.zip"),
       rel("data-1", "japan_00261120.csv", "neighbours_00261120.csv", "mushroomobserver_00261120.csv",
           "redlist_fungi_jp.json")]

class Matches(unittest.TestCase):
    def test_prefixes_are_exact(self):
        self.assertTrue(matches("japan_00261120.csv", "japan_"))
        self.assertFalse(matches("japan_dwca_1.zip", "japan_"))      # "japan_" must not eat DwC-A
        self.assertTrue(matches("japan_dwca_1.zip", "japan_dwca_"))
        self.assertTrue(matches("redlist_fungi_jp.json", "redlist_fungi_jp"))

class SelectInputs(unittest.TestCase):
    def test_complete_listing_picks_newest_release_per_prefix(self):
        picked = select_inputs(ALL)
        self.assertEqual(set(picked), set(PREFIXES))                  # required + optional all present
        self.assertEqual(picked["japan_"][0]["release"], "data-1")
        self.assertEqual(picked["eastasia_"][0]["release"], "data-2") # newest release carrying it

    def test_prefix_falls_back_to_older_release(self):
        releases = [rel("new", "eastasia_1.csv")] + ALL               # newest has no japan asset
        self.assertEqual(select_inputs(releases)["japan_"][0]["release"], "data-1")

    def test_incomplete_listing_names_missing_required(self):
        with self.assertRaises(StagingError) as ctx:
            select_inputs([ALL[0], rel("data-0", "japan_1.csv", "redlist_fungi_jp.json")])
        self.assertIn("mushroomobserver_", str(ctx.exception))
        self.assertIn("neighbours_", str(ctx.exception))

    def test_empty_listing_lists_all_required(self):
        with self.assertRaises(StagingError) as ctx:
            select_inputs([])
        for p, (_, req) in PREFIXES.items():
            if req: self.assertIn(p, str(ctx.exception))

class VerifyDestination(unittest.TestCase):
    def run_gh(self, result):
        with mock.patch("pipeline.fetch_latest_release_assets.subprocess.run", return_value=result) as run:
            info = verify_destination("owner/staging", "tok")
            return info, run

    def test_private_repo_passes_and_token_reaches_gh(self):
        info, run = self.run_gh(gh_result("true"))
        self.assertTrue(info["private"])
        self.assertEqual(run.call_args.kwargs["env"]["GH_TOKEN"], "tok")

    def test_public_repo_fails_closed(self):
        with self.assertRaises(StagingError) as ctx:
            self.run_gh(gh_result("false"))
        self.assertIn("public", str(ctx.exception))

    def test_missing_repo_404_fails_closed(self):
        with self.assertRaises(StagingError) as ctx:
            self.run_gh(gh_result(returncode=1, stderr=b"gh: Not Found (HTTP 404)\n"))
        self.assertIn("not accessible", str(ctx.exception))

    def test_bad_token_401_fails_closed(self):
        with self.assertRaises(StagingError) as ctx:
            self.run_gh(gh_result(returncode=1, stderr=b"gh: Bad credentials (HTTP 401)\n"))
        self.assertIn("not accessible", str(ctx.exception))

class MainFailsClosed(unittest.TestCase):
    def test_no_repo_configured(self):
        env = {k: v for k, v in os.environ.items() if k not in ("STAGING_REPO", "STAGING_TOKEN", "GH_TOKEN")}
        with mock.patch.dict(os.environ, env, clear=True), \
             mock.patch("sys.argv", ["fetch_latest_release_assets.py"]):
            with self.assertRaises(SystemExit) as ctx:
                main()
        self.assertIn("source repo", str(ctx.exception))

    def test_no_token_configured(self):
        with mock.patch.dict(os.environ, {"STAGING_REPO": "owner/staging"}, clear=True), \
             mock.patch("sys.argv", ["fetch_latest_release_assets.py"]):
            with self.assertRaises(SystemExit) as ctx:
                main()
        self.assertIn("token", str(ctx.exception))

    def test_verify_destination_cli_exits_clean_on_private(self):
        with mock.patch.dict(os.environ, {"STAGING_REPO": "owner/staging", "GH_TOKEN": "tok"}, clear=True), \
             mock.patch("sys.argv", ["fetch_latest_release_assets.py", "--verify-destination"]), \
             mock.patch("pipeline.fetch_latest_release_assets.subprocess.run",
                        return_value=gh_result("true")), \
             mock.patch("sys.stdout", new_callable=io.StringIO):
            main()                                                    # no SystemExit

if __name__ == "__main__":
    unittest.main()
