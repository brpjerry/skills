"""Tests for scripts/check.py's global-skill rules, against a throwaway repo layout."""
import contextlib
import importlib.util
import io
import os
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
spec = importlib.util.spec_from_file_location("check", ROOT / "scripts/check.py")
check = importlib.util.module_from_spec(spec)
spec.loader.exec_module(check)


class GlobalSkills(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        (self.root / "skills").mkdir()
        for link in check.LINKS:
            (self.root / link).parent.mkdir(exist_ok=True)
            os.symlink("../skills", self.root / link)
        self.saved = check.ROOT, check.GLOBAL
        check.ROOT, check.GLOBAL = self.root, self.root / "global"
        check.errors.clear()

    def tearDown(self):
        check.ROOT, check.GLOBAL = self.saved
        check.errors.clear()
        self.tmp.cleanup()

    def skill(self, where, name, explicit=True, openai=True):
        d = self.root / where / name
        (d / "agents").mkdir(parents=True)
        flag = "disable-model-invocation: true\n" if explicit else ""
        (d / "SKILL.md").write_text(f"---\nname: {name}\ndescription: Does a thing.\n{flag}---\n\n# {name}\n")
        if openai:
            (d / "agents/openai.yaml").write_text("policy:\n  allow_implicit_invocation: false\n")

    def run_check(self):
        check.errors.clear()
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            rc = check.main()
        return rc, list(check.errors)

    def test_explicit_global_skill_passes(self):
        self.skill("global", "tool")
        self.assertEqual(self.run_check(), (0, []))

    def test_global_skill_must_be_explicit_in_both_tools(self):
        self.skill("global", "no-flag", explicit=False)
        self.skill("global", "no-openai", openai=False)
        rc, errors = self.run_check()
        self.assertEqual(rc, 1)
        self.assertTrue(any("no-flag/SKILL.md" in e and "disable-model-invocation" in e for e in errors), errors)
        self.assertTrue(any("no-openai/agents/openai.yaml" in e for e in errors), errors)

    def test_names_cannot_collide_with_a_global_skill(self):
        self.skill("global", "tool")
        self.skill("skills", "tool")
        rc, errors = self.run_check()
        self.assertEqual(rc, 1)
        self.assertTrue(any("collides with the global skill" in e for e in errors), errors)


if __name__ == "__main__":
    unittest.main()
