"""Small, deterministic checks for the project skill and policy fixtures."""

import json
import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SKILL_PATH = ROOT / ".devin" / "skills" / "devin-orchestrator" / "SKILL.md"
RULE_PATH = ROOT / ".devin" / "rules" / "background-workers.md"
EVALS_PATH = ROOT / "evals" / "evals.json"


class SkillPolicyTests(unittest.TestCase):
    def test_frontmatter_uses_the_devin_project_skill_format(self):
        content = SKILL_PATH.read_text(encoding="utf-8")
        match = re.match(r"\A---\r?\n(.*?)\r?\n---\r?\n", content, re.DOTALL)

        self.assertIsNotNone(match, "SKILL.md must start with YAML frontmatter")
        frontmatter = match.group(1).splitlines()
        self.assertIn("name: devin-orchestrator", frontmatter)
        self.assertTrue(any(line.startswith("description:") for line in frontmatter))
        self.assertIn("triggers: [model, user]", frontmatter)
        self.assertFalse(any(line.strip() == "subagent: true" for line in frontmatter))

    def test_skill_contains_read_only_registry_contract(self):
        # The skill was de-attributed from the fan-out policy: it is now a
        # read-only console over the plan registry. Pin that contract.
        content = SKILL_PATH.read_text(encoding="utf-8").lower()
        required_markers = (
            "read-only",
            "plan registry",
            "devin-orchestrator history",
            "devin-orchestrator history --json",
            "--registry",
            "plans.jsonl",
            "never starts fan-out",
            "devin-orchestrator plan",
            "never records outcomes",
            "devin-orchestrator record",
            "does not dispatch or kill workers",
            "human cli action",
        )
        for marker in required_markers:
            with self.subTest(marker=marker):
                self.assertIn(marker, content)

    def test_rule_contains_fanout_delegation_and_safety_policy(self):
        # The delegation/safety policy the skill used to carry now lives
        # in the always-on rule; pin the markers there instead.
        content = RULE_PATH.read_text(encoding="utf-8").lower()
        required_markers = (
            "run_subagent",
            "is_background=true",
            "máximo 3 workers concorrentes",
            "sem aninhamento",
            "coleta obrigatória",
            "trivial inline",
            "devin_max_workers",
            "devin_inside_subagent",
            "devin-orchestrator plan",
            "ação humana via cli",
            ".sessions.json",
            "resume the recorded devin session",
            "never create another session",
            "live devin session database",
            "explicitly requests",
        )
        for marker in required_markers:
            with self.subTest(marker=marker):
                self.assertIn(marker, content)

    def test_eval_fixtures_cover_research_trivial_and_repo_session_cases(self):
        data = json.loads(EVALS_PATH.read_text(encoding="utf-8"))
        self.assertEqual(data["schema_version"], 1)
        self.assertIn("no model inference", data["description"].lower())
        cases = data["cases"]
        self.assertEqual(len(cases), 3)

        by_id = {case["id"]: case for case in cases}
        self.assertEqual(
            set(by_id),
            {
                "independent-read-only-research",
                "trivial-factual-question",
                "mapped-repo-fix-without-publication",
            },
        )
        for case in cases:
            self.assertTrue(case["prompt"].strip())
            expected = case["expected"]
            self.assertGreaterEqual(expected["worker_count"], 0)
            self.assertLessEqual(expected["worker_count"], 3)
            self.assertFalse(expected["nested_workers"])
            self.assertTrue(expected["parent_collects_and_reports"])

        research = by_id["independent-read-only-research"]["expected"]
        self.assertEqual(research["decision"], "delegate_background")
        self.assertEqual(research["worker_count"], 2)
        self.assertEqual(research["profiles"], ["subagent_explore", "subagent_explore"])

        trivial = by_id["trivial-factual-question"]["expected"]
        self.assertEqual(trivial["decision"], "inline_no_worker")
        self.assertEqual(trivial["worker_count"], 0)

        repo_change = by_id["mapped-repo-fix-without-publication"]["expected"]
        self.assertEqual(repo_change["repo_session_action"], "resume_exact_mapping")
        self.assertEqual(repo_change["profiles"], ["subagent_general"])
        self.assertEqual(repo_change["heavy_test_jobs_in_parallel"], 1)
        self.assertFalse(repo_change["commit_or_push"])


if __name__ == "__main__":
    unittest.main()
