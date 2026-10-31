"""Tests for global/repo-maintainer/scripts/maintainer.py. Stdlib only, no network: GitHub calls are faked,
and the checkout logic runs against throwaway git repos."""
import datetime as dt
import importlib.util
import os
import tempfile
import tomllib
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
spec = importlib.util.spec_from_file_location("maintainer", ROOT / "global/repo-maintainer/scripts/maintainer.py")
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)

# Test repos must not pick up the user's git config (signing, hooks, templates).
GIT_ENV = {"GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_NOSYSTEM": "1", "GIT_AUTHOR_NAME": "Test",
           "GIT_AUTHOR_EMAIL": "t@example.com", "GIT_COMMITTER_NAME": "Test", "GIT_COMMITTER_EMAIL": "t@example.com"}
m.ENV.update(GIT_ENV)


def git(cwd, *args, strip=True):
    return m.sh(["git", "-C", str(cwd), *args], strip=strip)


def config(**over):
    cfg = m.merge(m.load_toml(m.SCRIPT.parent / "defaults.toml"),
                  {"repo": "me/proj", "branch": "main", "remote": "origin", "slug": "me_proj"})
    cfg["models"] = m.load_toml(m.SKILL_DIR / "models.toml")
    return m.merge(cfg, over)


def pr(number=1, author="me", branch="maintainer/scan-x", head="a" * 40, checks="SUCCESS", comments=(),
       mergeable="MERGEABLE", review_decision=""):
    rollup = [] if checks is None else [{"__typename": "CheckRun", "name": "ci", "status": "COMPLETED",
                                         "conclusion": checks}]
    return {"number": number, "title": f"PR {number}", "url": f"https://github.com/me/proj/pull/{number}",
            "author": {"login": author}, "headRefName": branch, "headRefOid": head, "baseRefName": "main",
            "isDraft": False, "isCrossRepository": False, "mergeable": mergeable, "reviewDecision": review_decision,
            "statusCheckRollup": rollup, "createdAt": "2026-10-01T00:00:00Z", "updatedAt": "2026-10-02T00:00:00Z",
            "comments": list(comments), "reviews": []}


class Parsing(unittest.TestCase):
    def test_parse_remote(self):
        self.assertEqual(m.parse_remote("ssh://forgejo@192.168.0.74:2222/imouto/rockbox.git"),
                         ("192.168.0.74", 2222, "imouto/rockbox"))
        self.assertEqual(m.parse_remote("git@github.com:o/r.git"), ("github.com", 22, "o/r"))
        self.assertEqual(m.parse_remote("https://github.com/o/r"), ("github.com", 443, "o/r"))
        self.assertEqual(m.parse_remote("git://git.example.org/proj"), ("git.example.org", 9418, "proj"))
        self.assertIsNone(m.parse_remote("/srv/git/proj.git"))

    def test_github_repo_needs_github_itself(self):
        self.assertEqual(m.github_repo("https://github.com/o/r.git"), "o/r")
        self.assertEqual(m.github_repo("git@github.com:o/r.git"), "o/r")
        self.assertIsNone(m.github_repo("https://notgithub.com/o/r.git"))
        self.assertIsNone(m.github_repo("https://github.com.evil.example/o/r.git"))
        self.assertIsNone(m.github_repo("https://github.com/o/r/extra"))

    def test_slugs_keep_the_host_off_github(self):
        self.assertEqual(m.slugify("Me/Proj"), "me_proj")
        self.assertEqual(m.slugify("forge.internal/me/proj"), "forge.internal_me_proj")
        self.assertNotEqual(m.slugify("me/proj"), m.slugify("forge.internal/me/proj"))

    def test_remote_identity_compares_the_host(self):
        cfg = config(forge="git", repo="git.example.org/me/proj")
        self.assertEqual(m.remote_identity(cfg, "ssh://git@git.example.org/me/proj.git"), "git.example.org/me/proj")
        self.assertNotEqual(m.remote_identity(cfg, "ssh://git@other.example.org/me/proj.git"), cfg["repo"])

    def test_toml_str_round_trips(self):
        for value in ['/home/u/a"b', "back\\slash\\t", "tab\there", "ünïcode"]:
            self.assertEqual(tomllib.loads(f'x = "{m.toml_str(value)}"')["x"], value)

    def test_unit_str(self):
        self.assertEqual(m.unit_str('/a "b"/%i\\x'), '"/a \\"b\\"/%%i\\\\x"')
        self.assertEqual(m.unit_str("/opt/$HOME/run", exec_line=True), '"/opt/$$HOME/run"')
        self.assertEqual(m.unit_str("PATH=$HOME/bin"), '"PATH=$HOME/bin"')  # Environment= doesn't expand $

    def test_dep_files(self):
        for path in ["package.json", "web/yarn.lock", "Cargo.lock", ".github/workflows/ci.yml", "docker/Dockerfile"]:
            self.assertTrue(m.DEP_FILE.search(path), path)
        for path in ["src/main.rs", "README.md", "scripts/install.sh"]:
            self.assertFalse(m.DEP_FILE.search(path), path)


class PRGating(unittest.TestCase):
    def setUp(self):
        self.real_gh_pages = m.gh_pages
        m.gh_pages = lambda path: []  # no inline review comments unless a test says so
        self.now = dt.datetime(2026, 10, 31, 12, tzinfo=dt.timezone.utc)

    def tearDown(self):
        m.gh_pages = self.real_gh_pages

    def test_check_state(self):
        self.assertEqual(m.check_state(pr(checks=None)), ("none", []))
        self.assertEqual(m.check_state(pr(checks="FAILURE")), ("failure", ["ci"]))
        running = pr()
        running["statusCheckRollup"][0]["status"] = "IN_PROGRESS"
        self.assertEqual(m.check_state(running)[0], "pending")
        status = pr(checks=None)
        status["statusCheckRollup"] = [{"__typename": "StatusContext", "context": "ext", "state": "ERROR"}]
        self.assertEqual(m.check_state(status), ("failure", ["ext"]))

    def test_talk_ignores_own_posts_and_bot_commands_but_sees_edits(self):
        base = m.talk_key(pr())
        self.assertEqual(m.talk_key(pr(comments=[{"id": "c1", "body": f"done\n{m.MARKER}", "author": {"login": "me"}}])), base)
        self.assertEqual(m.talk_key(pr(comments=[{"id": "c2", "body": "@dependabot rebase", "author": {"login": "me"}}])), base)
        said = m.talk_key(pr(comments=[{"id": "c3", "body": "please rename", "author": {"login": "me"}}]))
        edited = m.talk_key(pr(comments=[{"id": "c3", "body": "please rename it", "author": {"login": "me"}}]))
        self.assertNotEqual(said, base)
        self.assertNotEqual(said, edited)
        inline = pr()
        inline["review_comments"] = [{"id": 9, "body": "nit", "user": {"login": "reviewer"}}]
        self.assertNotEqual(m.talk_key(inline), base)

    def test_settled_waits_for_the_grace_period(self):
        running = pr()
        running["statusCheckRollup"][0]["status"] = "QUEUED"
        pending = {}
        self.assertFalse(m.settled(running, pending, 6, self.now))
        self.assertFalse(m.settled(running, pending, 6, self.now + dt.timedelta(hours=5)))
        self.assertTrue(m.settled(running, pending, 6, self.now + dt.timedelta(hours=7)))
        self.assertTrue(m.settled(pr(), pending, 6, self.now))
        self.assertEqual(pending, {})

    def test_changed_prs_forgets_closed_prs(self):
        seen, pending = {"1": {"fp": "x"}, "7": {"fp": "y"}}, {"7": {"head": "h", "since": self.now.isoformat()}}
        out = m.changed_prs([pr(1)], seen, pending, 6, self.now)
        self.assertEqual([p["number"] for p, _, _ in out], [1])
        self.assertNotIn("7", seen)
        self.assertNotIn("7", pending)

    def gate(self, prs, state):
        ctx = m.Ctx(config(), dry_run=True)
        ctx.login, ctx.now, ctx._prs = "me", self.now, prs
        return m.gate_prs(ctx.cfg, None, state, ctx)

    def test_own_green_push_needs_no_agent(self):
        state = {}
        g = self.gate([pr(1), pr(2, author="someone-else"), pr(3, branch="feature")], state)
        self.assertEqual(g.status, "skipped")
        self.assertIn("1", state["prs"])  # recorded, so it isn't looked at again
        self.assertNotIn("2", state["prs"])  # other people's PRs are never this task's
        self.assertNotIn("3", state["prs"])  # nor the owner's own, non-maintainer PRs

    def test_feedback_failure_or_conflict_starts_the_agent(self):
        feedback = pr(comments=[{"id": "c1", "body": "rename this", "author": {"login": "me"}}])
        for case in [feedback, pr(checks="FAILURE"), pr(mergeable="CONFLICTING")]:
            g = self.gate([case], {})
            self.assertEqual(g.status, "work")
            self.assertEqual([p["number"] for p in g.context["prs"]], [1])


class BotVerification(unittest.TestCase):
    def setUp(self):
        self.real_gh_pages = m.gh_pages
        self.commits, self.files = [], ["package.json", "package-lock.json"]
        m.gh_pages = lambda path: self.commits if path.endswith("/commits") else [{"filename": f} for f in self.files]

    def tearDown(self):
        m.gh_pages = self.real_gh_pages

    def commit(self, sha="b" * 40, author="dependabot[bot]", committer="web-flow", verified=True, kind="minor"):
        message = f"Bump x\n\nupdated-dependencies:\n- dependency-name: x\n  update-type: version-update:semver-{kind}\n"
        return {"sha": sha, "author": {"login": author}, "committer": {"login": committer},
                "commit": {"message": message, "author": {"email": "e"}, "verification": {"verified": verified}}}

    def verify(self, **pr_over):
        return m.verify_bot_pr(config(), pr(author="app/dependabot", head="b" * 40) | pr_over)

    def test_genuine_bot_commit(self):
        self.commits = [self.commit()]
        facts = self.verify()
        self.assertTrue(facts["verified"], facts["problems"])
        self.assertEqual(facts["update_type"], "minor")
        self.assertTrue(facts["merge_allowed_by_policy"])

    def test_each_problem_blocks_verification(self):
        cases = {
            "not the bot": [self.commit(author="mallory")],
            "unsigned": [self.commit(verified=False)],
            "signed by someone else": [self.commit(committer="mallory")],
            "head moved": [self.commit(sha="c" * 40)],
        }
        for name, commits in cases.items():
            self.commits = commits
            self.assertFalse(self.verify()["verified"], name)
        self.commits, self.files = [self.commit()], ["package.json", "src/evil.js"]
        self.assertFalse(self.verify()["verified"], "non-manifest file")

    def test_unknown_update_counts_as_major(self):
        self.commits = [self.commit(kind="patch")]
        self.commits[0]["commit"]["message"] = "Bump the npm group"
        cfg = config(deps={"merge": ["patch", "minor"]})
        facts = m.verify_bot_pr(cfg, pr(author="app/dependabot", head="b" * 40))
        self.assertEqual(facts["update_type"], "unknown")
        self.assertFalse(facts["merge_allowed_by_policy"])


class Reports(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.real_state = m.STATE_DIR
        m.STATE_DIR = Path(self.tmp.name)
        self.repo = m.Repo("me_proj")
        self.repo.dir.mkdir(parents=True)
        self.tz = dt.timezone(dt.timedelta(hours=-7))

    def tearDown(self):
        m.STATE_DIR = self.real_state
        self.tmp.cleanup()

    def run_on(self, day, task, status, hhmm="10:00"):
        self.repo.record({"task": task, "date": day, "start": f"{day}T{hhmm}:00", "status": status, "note": "n"})

    def test_due_days(self):
        cfg, t = config(), dt.datetime(2026, 10, 31, 12, tzinfo=self.tz)
        self.run_on("2026-10-20", "prs", "ok")  # older than catch_up_days
        self.run_on("2026-10-29", "prs", "skipped")
        self.run_on("2026-10-30", "deps", "ok")
        self.run_on("2026-10-31", "prs", "ok")
        self.run_on("2026-10-31", "report", "done")
        self.assertEqual(m.due_days(cfg, self.repo, {}, t), ["2026-10-29", "2026-10-30"])
        self.assertIn("2026-10-31", m.due_days(cfg, self.repo, {}, t.replace(hour=23, minute=55)))
        state = {"reports": {"2026-10-29": 0, "2026-10-30": 1, "2026-10-31": 1}}
        self.assertEqual(m.due_days(cfg, self.repo, state, t.replace(day=31, hour=23, minute=55)), [])
        self.run_on("2026-10-31", "deps", "ok", "23:58")
        self.assertEqual(m.due_days(cfg, self.repo, state, dt.datetime(2026, 11, 1, 9, tzinfo=self.tz)), ["2026-10-31"])

    def test_backoff(self):
        last = dt.datetime(2026, 10, 31, 10, tzinfo=self.tz)
        later = last + dt.timedelta(minutes=30)
        for count, hours in [(2, None), (3, 2), (5, 8), (9, 24)]:
            state = {"failures": {"prs": {"count": count, "last": last.isoformat()}}}
            until = m.paused_until(state, "prs", later)
            self.assertEqual(until and (until - last).total_seconds() / 3600, hours)

    def test_quiet_report_lists_standing_attention(self):
        text = m.quiet_report(config(), self.repo, "2026-10-31", [], {"prs": 3}, {}, ["**Idle PRs:** #4"])
        self.assertIn("## Needs your attention", text)
        self.assertIn("#4", text)
        self.assertIn("prs ×3", text)
        self.assertIn("Quiet day", m.quiet_report(config(), self.repo, "2026-10-31", [], {"prs": 3}, {}))


class Checkout(unittest.TestCase):
    """restore, recover and update_local against real git repos."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)
        self.co = self.dir / "co"
        git(self.dir, "init", "-q", "-b", "main", "co")
        (self.co / "a.txt").write_text("one\n")
        git(self.co, "add", "a.txt")
        git(self.co, "commit", "-qm", "init")
        self.repo = m.Repo("t")
        self.repo.checkout = self.co
        self.cfg = config()

    def tearDown(self):
        self.tmp.cleanup()

    def head(self, cwd=None):
        return git(cwd or self.co, "symbolic-ref", "--quiet", "--short", "HEAD")

    def test_restore_stashes_only_the_agents_leftovers(self):
        (self.co / "notes.txt").write_text("the owner's\n")
        (self.co / "abc.txt").write_text("the owner's too\n")
        pos = m.position(self.repo)
        git(self.co, "switch", "-q", "--detach", "main")
        (self.co / "a.txt").write_text("changed by the agent\n")
        (self.co / " lead.txt").write_text("agent\n")
        (self.co / "a*.txt").write_text("agent\n")  # as a pathspec this would also match abc.txt
        notes = m.restore(self.repo, pos, "test")
        self.assertEqual(self.head(), "main")
        self.assertTrue((self.co / "notes.txt").exists() and (self.co / "abc.txt").exists())
        self.assertFalse((self.co / " lead.txt").exists() or (self.co / "a*.txt").exists())
        stashed = git(self.co, "stash", "show", "--include-untracked", "--name-only", "stash@{0}", strip=False)
        self.assertEqual(set(stashed.splitlines()), {"a.txt", " lead.txt", "a*.txt"})
        self.assertTrue(any("stashed 3" in n for n in notes))

    def test_restore_undoes_an_unfinished_merge(self):
        git(self.co, "switch", "-qc", "side")
        (self.co / "a.txt").write_text("side\n")
        git(self.co, "commit", "-qam", "side")
        git(self.co, "switch", "-q", "main")
        pos = m.position(self.repo)
        git(self.co, "switch", "-q", "--detach", "main")
        (self.co / "a.txt").write_text("main\n")
        git(self.co, "commit", "-qam", "main")
        m.sh(["git", "-C", str(self.co), "merge", "side"], check=False)  # conflicts
        self.assertTrue(m.operations(self.repo))
        m.restore(self.repo, pos, "test")
        self.assertEqual(m.operations(self.repo), [])
        self.assertEqual(self.head(), "main")

    def test_recover_from_the_agents_own_branch(self):
        state = {"position": m.position(self.repo)}
        git(self.co, "switch", "-qc", "maintainer/scan-x")
        self.assertIn("recovered", m.recover(self.cfg, self.repo, state))
        self.assertEqual(self.head(), "main")
        self.assertNotIn("position", state)

    def test_recover_leaves_the_owners_new_branch_alone(self):
        state = {"position": m.position(self.repo)}
        git(self.co, "switch", "-qc", "owner-work")
        self.assertIsNone(m.recover(self.cfg, self.repo, state))
        self.assertEqual(self.head(), "owner-work")
        self.assertNotIn("position", state)

    def test_failed_recovery_is_kept_for_the_next_run(self):
        state = {"position": {"head": "deleted-branch", "detached": False, "untracked": []}}
        git(self.co, "switch", "-q", "--detach", "main")
        with self.assertRaises(m.Failure):
            m.recover(self.cfg, self.repo, state)
        self.assertIn("position", state)

    def test_update_local_fast_forwards_only_clean_worktrees(self):
        bare = self.dir / "remote.git"
        git(self.dir, "init", "-q", "--bare", "-b", "main", "remote.git")
        git(self.co, "remote", "add", "origin", str(bare))
        for b in ("b1", "b2", "b3"):
            git(self.co, "branch", b)
        git(self.co, "push", "-q", "origin", "main", "b1", "b2", "b3")
        git(self.co, "worktree", "add", "-q", str(self.dir / "wt1"), "b1")
        git(self.co, "worktree", "add", "-q", str(self.dir / "wt2"), "b2")
        (self.dir / "wt2" / "a.txt").write_text("the owner's work in progress\n")
        other = self.dir / "other"
        git(self.dir, "clone", "-q", str(bare), "other")
        for b in ("main", "b1", "b2", "b3"):
            git(other, "switch", "-q", b)
            (other / f"{b}.txt").write_text(b)
            git(other, "add", ".")
            git(other, "commit", "-qm", f"advance {b}")
            git(other, "push", "-q", "origin", b)
        git(self.co, "fetch", "-q", "origin")
        notes = m.update_local(self.cfg, self.repo)
        tip = lambda ref: git(self.co, "rev-parse", ref)
        for b in ("main", "b1", "b3"):  # main is checked out in the clean main worktree, b3 nowhere
            self.assertEqual(tip(b), tip(f"origin/{b}"), b)
        self.assertNotEqual(tip("b2"), tip("origin/b2"))
        self.assertEqual((self.dir / "wt2" / "a.txt").read_text(), "the owner's work in progress\n")
        self.assertTrue((self.dir / "wt1" / "b1.txt").exists())
        self.assertTrue(any("left local b2" in n for n in notes))


if __name__ == "__main__":
    unittest.main()
