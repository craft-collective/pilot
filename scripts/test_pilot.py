"""Tests for pilot.py. The sync tests run against local bare repositories standing in for
upstream and the org, so they need git but not GitHub.

    python3 -m unittest discover -s scripts
"""

import datetime
import subprocess
import sys
import tempfile
import types
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import pilot  # noqa: E402


class Rules(unittest.TestCase):
    def test_a_sync_is_reviewed_by_someone_who_didnt_do_it(self):
        members = ["a", "b", "c"]
        picked = pilot.sync_reviewer(members, "2026-11-02-1630", {"a"})
        self.assertIn(picked, ["b", "c"])
        self.assertEqual(picked, pilot.sync_reviewer(members, "2026-11-02-1630", {"a"}))
        self.assertEqual(pilot.sync_reviewer(members, "x", {"a", "b"}), "c")
        self.assertIsNone(pilot.sync_reviewer(["a"], "x", {"a"}))

    def test_credit_goes_to_who_did_the_work_once_promoted(self):
        state = {"syncer": "a", "promoter": "c",
                 "branches": [{"status": "promoted", "resolver": "b"}, {"status": "promoted"}]}
        self.assertEqual(pilot.sync_credit(state),
                         [("a", "syncs"), ("b", "resolutions"), ("c", "sync reviews")])
        self.assertEqual(pilot.sync_credit({**state, "promoter": None}), [])

    def test_patch_reviewers_exclude_the_author_and_repeat(self):
        members = ["a", "b", "c", "d"]
        first = pilot.patch_reviewers(members, "B", "org/x#1", 2)
        self.assertEqual(first, pilot.patch_reviewers(members, "b", "org/x#1", 2))
        self.assertEqual(len(first), 2)
        self.assertNotIn("b", first)
        self.assertEqual(pilot.patch_reviewers(["a"], "a", "k", 2), [])

    def test_rewrite_pins(self):
        url = "https://github.com/storytold/craft-libs"
        text = (f'craft-ui = {{ git = "{url}", rev = "{"1" * 40}", features = ["serde"] }}\n'
                f'krilla = {{ version = "0.8", git = "{url}.git", rev = "{"1" * 40}" }}\n'
                'serde = "1"\n'
                'other = { git = "https://github.com/storytold/craft-libs-extra", rev = "abcdef1" }\n')
        out, count = pilot.rewrite_pins(text, url, "https://github.com/org/craft-libs", "2" * 40)
        self.assertEqual(count, 2)
        self.assertEqual(out.count('git = "https://github.com/org/craft-libs"'), 2)
        self.assertEqual(out.count("2" * 40), 2)
        self.assertIn('features = ["serde"]', out)
        self.assertIn("craft-libs-extra", out)
        self.assertEqual(pilot.pinned_revs(text, url), ["1" * 40, "1" * 40])

    def test_a_pin_without_an_inline_rev_fails_closed(self):
        url = "https://github.com/storytold/craft-libs"
        with self.assertRaises(pilot.SyncError):
            pilot.rewrite_pins(f'craft-ui = {{ git = "{url}", branch = "main" }}\n', url, "x", "y")

    def test_the_real_config_is_sound(self):
        self.assertEqual(pilot.validate(pilot.load_config()), [])

    def test_solo_phase_ends_with_a_second_member(self):
        cfg = pilot.load_config()
        pilot.fork_by_id(cfg, "web-host")["members"].append("someone")
        self.assertTrue(any("solo" in p for p in pilot.validate(cfg)))
        cfg["review"] = {"approvals": 1}
        pilot.fork_by_id(cfg, "web-host")["members"].pop()
        self.assertTrue(any("at least 2" in p for p in pilot.validate(cfg)))

    def test_validate_catches_a_pin_on_a_stranger(self):
        cfg = pilot.load_config()
        pilot.fork_by_id(cfg, "pbr-photocraft")["branches"][0]["pins"] = {"craft-libs": "web-host"}
        self.assertTrue(any("isn't a parent" in p for p in pilot.validate(cfg)))


class Decisions(unittest.TestCase):
    def setUp(self):
        self.cfg = pilot.load_config()
        pilot.fork_by_id(self.cfg, "web-host")["members"] = ["a", "b", "c"]
        self.rules = {"hours": 48, "quorum": 3, "majority": 0.67}
        self.t0 = datetime.datetime(2026, 11, 2, tzinfo=datetime.timezone.utc)

    def files(self, *pairs):
        return [{"filename": p, "status": st} for p, st in pairs]

    def test_scopes(self):
        self.assertEqual(pilot.classify(self.files(("PROCESS.md", "modified")), self.cfg),
                         ("process", None, False))
        self.assertEqual(pilot.classify(self.files(("forks/web-host/adr/0002-bridge-base.md", "added")),
                                        self.cfg), ("fork", "web-host", False))
        new = self.files(("forks/pbr-raster/fork.json", "added"), ("forks/pbr-raster/adr/0001-raster.md", "added"))
        self.assertEqual(pilot.classify(new, self.cfg), ("fork", "pbr-raster", True))

    def test_a_pr_spans_one_scope(self):
        with self.assertRaisesRegex(pilot.SyncError, "split it"):
            pilot.classify(self.files(("forks/web-host/adr/0002-x.md", "added"),
                                      ("forks/pbr-engine/adr/0002-y.md", "added")), self.cfg)
        with self.assertRaisesRegex(pilot.SyncError, "split it"):
            pilot.classify(self.files(("forks/web-host/adr/0002-x.md", "added"), ("README.md", "modified")), self.cfg)

    def test_adopted_records_are_frozen(self):
        draft = self.files(("forks/web-host/adr/0001-web-host.md", "modified"))
        self.assertEqual(pilot.classify(draft, self.cfg)[0], "fork")
        pilot.fork_by_id(self.cfg, "web-host")["status"] = "open"
        with self.assertRaisesRegex(pilot.SyncError, "can't change"):
            pilot.classify(draft, self.cfg)
        with self.assertRaisesRegex(pilot.SyncError, "can't change"):
            pilot.classify(self.files(("forks/web-host/adr/0002-x.md", "modified")), self.cfg)
        with self.assertRaisesRegex(pilot.SyncError, "can't change"):
            pilot.classify(self.files(("forks/web-host/adr/0001-web-host.md", "removed")), self.cfg)

    def test_voters(self):
        self.assertEqual(pilot.voters(self.cfg, "fork", "web-host", False, "x", []), ["a", "b", "c"])
        self.assertEqual(pilot.voters(self.cfg, "fork", "new", True, "x", ["y"]), ["x", "y"])
        self.assertIn("a", pilot.voters(self.cfg, "process", None, False, "x", []))

    def state(self, *votes, head="h1"):
        return {"heads": {"h1": self.t0.isoformat(), "h2": (self.t0 + datetime.timedelta(hours=60)).isoformat()},
                "votes": {u: {"choice": c, "reason": "r", "sha": sha} for u, c, sha in votes}}

    def test_tally_waits_for_the_discussion_and_a_quorum(self):
        later = self.t0 + datetime.timedelta(hours=49)
        passed, waits, _, _ = pilot.tally(self.state(("a", "yes", "h1"), ("b", "yes", "h1")), "h1",
                                          ["a", "b", "c"], self.rules, later)
        self.assertFalse(passed)
        self.assertEqual(waits, ["2 of the 3 votes needed"])
        passed, waits, _, _ = pilot.tally(self.state(("a", "yes", "h1"), ("b", "yes", "h1"), ("c", "yes", "h1")),
                                          "h1", ["a", "b", "c"], self.rules, self.t0)
        self.assertFalse(passed)
        self.assertIn("discussion stays open", waits[0])

    def test_tally_majority_and_resets(self):
        later = self.t0 + datetime.timedelta(hours=200)
        three = [("a", "yes", "h1"), ("b", "yes", "h1"), ("c", "no", "h1")]
        self.assertFalse(pilot.tally(self.state(*three), "h1", ["a", "b", "c"], self.rules, later)[0])
        self.assertTrue(pilot.tally(self.state(*three), "h1", ["a", "b", "c"],
                                    {**self.rules, "majority": 0.6}, later)[0])
        # Votes on an earlier head don't count, and a vote from a non-member never does.
        self.assertFalse(pilot.tally(self.state(*three), "h2", ["a", "b", "c"], self.rules, later)[0])
        self.assertFalse(pilot.tally(self.state(("a", "yes", "h1"), ("z", "yes", "h1"), ("y", "yes", "h1")),
                                     "h1", ["a", "b", "c"], self.rules, later)[0])
        self.assertFalse(pilot.tally(self.state(), "h1", [], self.rules, later)[0])

    def test_a_no_needs_a_reason(self):
        self.assertEqual(pilot.parse_vote("/vote yes"), ("yes", ""))
        self.assertEqual(pilot.parse_vote("/vote no  the bridge must stay off by default"),
                         ("no", "the bridge must stay off by default"))
        with self.assertRaises(pilot.SyncError):
            pilot.parse_vote("/vote no")
        with self.assertRaises(pilot.SyncError):
            pilot.parse_vote("/vote maybe")


def sh(cwd, *args):
    return subprocess.run(args, cwd=cwd, check=True, capture_output=True, text=True,
                          env={**pilot.BOT_ENV, "PATH": "/usr/bin:/bin:/usr/local/bin:/opt/homebrew/bin",
                               "HOME": str(cwd)}).stdout.strip()


class App(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.key = Path(self.tmp.name) / "app.pem"
        subprocess.run(["openssl", "genrsa", "-out", str(self.key), "2048"], check=True, capture_output=True)
        self.app = pilot.App(12345, self.key.read_text(), "org")

    def tearDown(self):
        self.tmp.cleanup()

    def test_the_jwt_is_signed_with_the_apps_key(self):
        import base64
        import json
        signing, _, sig = self.app.jwt(now=1_700_000_000).rpartition(".")
        payload = json.loads(base64.urlsafe_b64decode(signing.split(".")[1] + "=="))
        self.assertEqual(payload, {"iat": 1_699_999_940, "exp": 1_700_000_540, "iss": "12345"})
        pub = Path(self.tmp.name) / "app.pub"
        subprocess.run(["openssl", "rsa", "-in", str(self.key), "-pubout", "-out", str(pub)],
                       check=True, capture_output=True)
        sigfile = Path(self.tmp.name) / "sig"
        sigfile.write_bytes(base64.urlsafe_b64decode(sig + "=" * (-len(sig) % 4)))
        r = subprocess.run(["openssl", "dgst", "-sha256", "-verify", str(pub), "-signature", str(sigfile)],
                           input=signing.encode(), capture_output=True)
        self.assertEqual(r.returncode, 0, r.stderr)

    def test_a_token_is_reused_until_it_nearly_runs_out(self):
        minted = []

        def call(path, method="GET"):
            if path == "orgs/org/installation":
                return {"id": 7}
            minted.append(path)
            expires = datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(minutes=self.ttl)
            return {"token": f"t{len(minted)}", "expires_at": expires.isoformat().replace("+00:00", "Z")}

        self.app._call = call
        self.ttl = 60
        self.assertEqual([self.app.token(), self.app.token()], ["t1", "t1"])
        self.assertEqual(minted, ["app/installations/7/access_tokens"])
        self.app._expires = 0  # an hour later
        self.assertEqual(self.app.token(), "t2")

    def test_app_id_is_a_number(self):
        cfg = pilot.load_config()
        cfg["app_id"] = "12345"
        self.assertTrue(any("app_id" in p for p in pilot.validate(cfg)))


class FakeGitHub(pilot.GitHub):
    """Real git against local repositories; issue traffic is recorded, not sent."""

    def __init__(self, cfg, base):
        super().__init__(cfg, f"file://{base}")
        self.base = Path(base)
        self.calls = []

    def file(self, owner, repo, path, ref="main"):
        return sh(self.base, "git", "--git-dir", str(self.base / owner / repo), "show", f"{ref}:{path}")

    def open_sync_issue(self, fork_id):
        return None

    def edit_issue(self, number, body=None, **kw):
        self.calls.append(("edit", number, body, kw))

    def comment(self, number, body, repo=None):
        self.calls.append(("comment", number, body))

    def close_issue(self, number, comment):
        self.calls.append(("close", number, comment))



class Sync(unittest.TestCase):
    """upstream `up` and the org `org`, each with craft-libs and an app that pins it."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.base = Path(self.tmp.name)
        self.lib_url = f"file://{self.base}/up/craft-libs"
        self.cfg = {
            "org": "org", "upstream_owner": "up", "review": {"approvals": 2},
            "repos": {"craft-libs": {"check": "true"}, "app": {"check": "true"}},
            "forks": [
                {"id": "engine", "adr": "x", "status": "open", "parents": [], "members": ["a", "b"],
                 "branches": [{"repo": "craft-libs", "branch": "fork/engine", "base": "pinned-by:app"}]},
                {"id": "app-int", "adr": "x", "status": "open", "parents": ["engine"], "members": ["a", "b"],
                 "branches": [{"repo": "app", "branch": "fork/app-int", "base": "main",
                               "pins": {"craft-libs": "engine"}}]},
            ],
        }
        self.gh = FakeGitHub(self.cfg, self.base)
        self.work = self.base / "work"
        self.work.mkdir()

        # craft-libs upstream: two commits; the apps pin the first.
        self.lib = self.repo("up", "craft-libs", {"lib.rs": "pub fn a() {}\n"})
        self.pinned = sh(self.lib, "git", "rev-parse", "HEAD")
        self.commit(self.lib, {"lib.rs": "pub fn a() {}\npub fn newer() {}\n"}, "newer")
        sh(self.lib, "git", "push", "-q", "origin", "HEAD:main")
        self.fork_repo("craft-libs", "fork/engine", self.pinned)

        manifest = f'[dependencies]\ncraft-ui = {{ git = "{self.lib_url}", rev = "{self.pinned}" }}\n'
        self.app = self.repo("up", "app", {"Cargo.toml": manifest, "a.txt": "one\ntwo\nthree\n"})
        self.app_base = sh(self.app, "git", "rev-parse", "HEAD")
        self.fork_repo("app", "fork/app-int", self.app_base)

    def tearDown(self):
        self.tmp.cleanup()

    def repo(self, owner, name, files):
        bare = self.base / owner / name
        bare.mkdir(parents=True)
        sh(bare, "git", "init", "-q", "--bare", "-b", "main")
        wd = self.base / "src" / owner / name
        wd.mkdir(parents=True)
        sh(wd, "git", "init", "-q", "-b", "main")
        sh(wd, "git", "remote", "add", "origin", str(bare))
        self.commit(wd, files, "initial")
        sh(wd, "git", "push", "-q", "origin", "HEAD:main")
        return wd

    def commit(self, wd, files, message):
        for path, text in files.items():
            (wd / path).write_text(text)
        sh(wd, "git", "add", "-A")
        sh(wd, "git", "commit", "-q", "-m", message)
        return sh(wd, "git", "rev-parse", "HEAD")

    def fork_repo(self, name, branch, at):
        bare = self.base / "org" / name
        bare.parent.mkdir(exist_ok=True)
        sh(self.base, "git", "clone", "-q", "--bare", str(self.base / "up" / name), str(bare))
        sh(bare, "git", "update-ref", f"refs/heads/{branch}", at)

    def org_checkout(self, name, ref):
        wd = self.base / "dev" / name
        if not wd.exists():
            sh(self.base, "git", "clone", "-q", str(self.base / "org" / name), str(wd))
        sh(wd, "git", "fetch", "-q", "origin")
        sh(wd, "git", "checkout", "-q", "-B", "work", f"origin/{ref}")
        return wd

    def admit(self, name, branch, files, message):
        """A patch squash-merged into a fork branch."""
        wd = self.org_checkout(name, branch)
        sha = self.commit(wd, files, message)
        sh(wd, "git", "push", "-q", "origin", f"HEAD:refs/heads/{branch}")
        return sha

    def ref(self, owner, name, ref):
        return sh(self.base, "git", "--git-dir", str(self.base / owner / name), "rev-parse", ref)

    def show(self, owner, name, ref, path):
        return sh(self.base, "git", "--git-dir", str(self.base / owner / name), "show", f"{ref}:{path}")

    def sync(self, fork_id, date="2026-11-02"):
        fork = pilot.fork_by_id(self.cfg, fork_id)
        records = pilot.sync_fork(self.gh, fork, self.work / fork_id / date, date, refresh_lock=False)
        self.range_diffs = pilot.take_range_diffs(records)
        return records

    def test_engine_syncs_onto_the_revision_the_apps_pin(self):
        self.admit("craft-libs", "fork/engine", {"pbr.rs": "pub fn shade() {}\n"}, "craft-pbr")
        [record] = self.sync("engine")
        self.assertEqual(record["status"], "clean")
        self.assertEqual(record["new_base"], self.pinned)
        self.assertNotIn("newer", self.show("org", "craft-libs", "sync/engine/2026-11-02", "lib.rs"))
        self.assertEqual(self.show("org", "craft-libs", "sync/engine/2026-11-02", ".github/CODEOWNERS").splitlines()[-1],
                         "* @org/engine")

    def test_cargo_env_joins_an_existing_config(self):
        path = Path(self.tmp.name) / "config.toml"
        path.write_text('[alias]\nxtask = "run --package xtask --"\n\n[env]\nRUST_LOG = "warn"\n')
        pilot.set_cargo_env(path, {"CRAFT_LIBS": ("https://github.com/org/craft-libs", "abc")})
        text = path.read_text()
        self.assertEqual(text.count("[env]"), 1)
        self.assertIn('[env]\n# Written', text)
        self.assertIn('CRAFT_LIBS_REV = "abc"\nRUST_LOG = "warn"\n', text)
        self.assertTrue(text.startswith('[alias]\nxtask'))

    def test_a_first_sync_with_no_patches_shows_the_metadata_commit(self):
        [record] = self.sync("engine")
        self.assertEqual(record["status"], "clean")
        [(repo, rd)] = self.range_diffs
        self.assertEqual(repo, "craft-libs")
        self.assertIn("No patches before the sync", rd)
        self.assertIn(pilot.METADATA_SUBJECT, rd)
        self.assertIn("+* @org/engine", rd)

    def test_child_pins_the_parents_head_and_drops_upstreamed_patches(self):
        engine_head = self.ref("org", "craft-libs", "fork/engine")
        p1 = self.admit("app", "fork/app-int", {"b.txt": "bee\n"}, "add b")
        self.admit("app", "fork/app-int", {"a.txt": "ONE\ntwo\nthree\n"}, "capitalise one")
        self.commit(self.app, {"b.txt": "bee\n"}, "upstream takes b")  # upstream merges p1
        sh(self.app, "git", "push", "-q", "origin", "HEAD:main")

        [record] = self.sync("app-int")
        self.assertEqual(record["status"], "clean")
        self.assertEqual(record["dropped"], [p1])
        manifest = self.show("org", "app", "sync/app-int/2026-11-02", "Cargo.toml")
        self.assertIn(f'git = "file://{self.base}/org/craft-libs", rev = "{engine_head}"', manifest)
        self.assertEqual(self.show("org", "app", "sync/app-int/2026-11-02", "a.txt"), "ONE\ntwo\nthree")
        config = self.show("org", "app", "sync/app-int/2026-11-02", ".cargo/config.toml")
        self.assertIn(f'[env]\n# Written', config)
        self.assertIn(f'CRAFT_LIBS_GIT = "file://{self.base}/org/craft-libs"\nCRAFT_LIBS_REV = "{engine_head}"', config)

        # A second sync with nothing new reports the branch as current once it is promoted.
        self.gh.cfg["forks"][1]["branches"][0]["branch"] = "fork/app-int"
        sh(self.base, "git", "--git-dir", str(self.base / "org" / "app"), "update-ref",
           "refs/heads/fork/app-int", record["head"])
        [again] = self.sync("app-int", "2026-11-09")
        self.assertEqual(again["status"], "current")

    def test_conflict_ready_promote(self):
        self.admit("app", "fork/app-int", {"a.txt": "ONE\ntwo\nthree\n"}, "capitalise one")
        self.commit(self.app, {"a.txt": "uno\ntwo\nthree\n"}, "upstream renames one")
        sh(self.app, "git", "push", "-q", "origin", "HEAD:main")

        [record] = self.sync("app-int")
        self.assertEqual(record["status"], "conflict")
        self.assertEqual(record["files"], ["a.txt"])
        state = {"fork": "app-int", "date": "2026-11-02", "syncer": "a", "reviewer": None,
                 "branches": [record]}
        body = pilot.render_issue(self.gh, pilot.fork_by_id(self.cfg, "app-int"), state)
        self.assertEqual(pilot.read_state(body), state)

        # A promote before the resolution is refused.
        fork = pilot.fork_by_id(self.cfg, "app-int")
        args = types.SimpleNamespace(issue=1, user="a", no_lock=True)
        with self.assertRaises(pilot.SyncError):
            pilot.promote(self.gh, fork, state, args)

        # Member a resolves on the sync branch, following the issue's instructions.
        wd = self.org_checkout("app", record["sync"])
        sh(wd, "git", "fetch", "-q", "origin", record["branch"])
        subprocess.run(["git", "cherry-pick", *record["remaining"]], cwd=wd, capture_output=True,
                       env={**pilot.BOT_ENV, "PATH": "/usr/bin:/bin:/usr/local/bin:/opt/homebrew/bin"})
        (wd / "a.txt").write_text("UNO\ntwo\nthree\n")
        sh(wd, "git", "add", "a.txt")
        sh(wd, "git", "-c", "core.editor=true", "cherry-pick", "--continue")
        sh(wd, "git", "push", "-q", "origin", f"HEAD:refs/heads/{record['sync']}")

        pilot.ready(self.gh, fork, state, args)
        resolved = state["branches"][0]
        self.assertEqual(resolved["status"], "resolved")
        self.assertEqual(resolved["resolver"], "a")
        self.assertEqual(state["reviewer"], "b")  # the only member who neither synced nor resolved
        self.assertEqual(self.show("org", "app", record["sync"], "a.txt"), "UNO\ntwo\nthree")
        self.assertIn("range-diff", self.gh.calls[-1][2])

        # The resolver can't promote their own resolution; the other member can.
        with self.assertRaises(pilot.SyncError):
            pilot.promote(self.gh, fork, state, args)
        old_head = record["old_head"]
        pilot.promote(self.gh, fork, state, types.SimpleNamespace(issue=1, user="b", no_lock=True))
        self.assertEqual(self.ref("org", "app", "fork/app-int"), resolved["head"])
        self.assertEqual(self.ref("org", "app", "archive/app-int/2026-11-02"), old_head)
        self.assertEqual(self.gh.calls[-1][0], "close")

    def test_the_syncer_cant_promote_a_clean_sync(self):
        self.admit("app", "fork/app-int", {"b.txt": "bee\n"}, "add b")
        self.commit(self.app, {"c.txt": "sea\n"}, "upstream moves")
        sh(self.app, "git", "push", "-q", "origin", "HEAD:main")
        [record] = self.sync("app-int")
        self.assertEqual(record["status"], "clean")
        self.assertEqual(self.range_diffs[0][0], "app")
        self.assertIn("add b", self.range_diffs[0][1])
        state = {"fork": "app-int", "date": "2026-11-02", "syncer": "a", "branches": [record]}
        fork = pilot.fork_by_id(self.cfg, "app-int")
        with self.assertRaisesRegex(pilot.SyncError, "ran this sync"):
            pilot.promote(self.gh, fork, state, types.SimpleNamespace(issue=1, user="a", no_lock=True))
        pilot.promote(self.gh, fork, state, types.SimpleNamespace(issue=1, user="b", no_lock=True))
        self.assertEqual(pilot.sync_credit(state), [("a", "syncs"), ("b", "sync reviews")])

    def test_promote_refuses_when_the_fork_moved(self):
        self.admit("app", "fork/app-int", {"b.txt": "bee\n"}, "add b")
        self.commit(self.app, {"c.txt": "sea\n"}, "upstream moves")
        sh(self.app, "git", "push", "-q", "origin", "HEAD:main")
        [record] = self.sync("app-int")
        state = {"fork": "app-int", "date": "2026-11-02", "syncer": "b", "branches": [record]}
        self.admit("app", "fork/app-int", {"d.txt": "dee\n"}, "merged during the sync")
        fork = pilot.fork_by_id(self.cfg, "app-int")
        with self.assertRaisesRegex(pilot.SyncError, "new commits"):
            pilot.promote(self.gh, fork, state, types.SimpleNamespace(issue=1, user="a", no_lock=True))


if __name__ == "__main__":
    unittest.main()
