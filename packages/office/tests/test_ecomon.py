import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from devin_office import ecomon


def _tools(cmdlines):
    with patch.object(ecomon, "_proc_cmdlines", return_value=cmdlines), \
         patch.object(ecomon, "_units", return_value={}), \
         patch.object(ecomon, "_meta", return_value={}), \
         patch.object(ecomon, "_probe", return_value=None):
        return ecomon.collect_eco()[ecomon.HOST_LABEL]["tools"]


class TestToolPatterns(unittest.TestCase):
    def test_absorbed_servers_alive_via_unified(self):
        tools = _tools(["python3 /x/mcp/unified-server.py"])
        for name in ("unified-mcp", "memory-mcp", "nlsql-mcp",
                     "obsidian-mcp", "gh-bridge", "ecc-bridge"):
            self.assertTrue(tools[name], name)

    def test_standalone_spawn_still_matched(self):
        tools = _tools(["python3 /x/mcp/obsidian-bridge-server.py"])
        self.assertTrue(tools["obsidian-mcp"])
        self.assertFalse(tools["unified-mcp"])

    def test_frame_ronin_matched(self):
        tools = _tools(
            ["python -c from frame_ronin_mcp.server import main; main()"])
        self.assertTrue(tools["frame-ronin"])

    def test_vm_tunnel_matches_ssh_forward(self):
        tools = _tools(
            [("ssh -N -L 11435:localhost:11434 -L 8790:localhost:8790 "
              "devin-vm -o BatchMode=yes")])
        self.assertTrue(tools["vm-tunnel"])

    def test_no_match_when_absent(self):
        tools = _tools(["/usr/bin/earlyoom"])
        self.assertFalse(tools["vm-tunnel"])
        self.assertFalse(tools["unified-mcp"])


class TestMeta(unittest.TestCase):
    def _meta(self, td):
        with patch.dict(os.environ, {"OFFICE_ECOSYSTEM_DIR": td}):
            os.environ.pop("OFFICE_REGISTRY_JSON", None)
            return ecomon._meta()

    def test_retired_clones_not_counted_as_repos(self):
        with tempfile.TemporaryDirectory() as td:
            eco = Path(td)
            for name in ("devin-evals", "devin-office", "devin-dream"):
                (eco / name / ".git").mkdir(parents=True)
            reg = eco / "devin-powerups"
            (reg / ".git").mkdir(parents=True)
            (reg / "registry.json").write_text(json.dumps({
                "repositories": [{"name": n} for n in
                                 ("devin-evals", "devin-office",
                                  "devin-powerups")]}))
            meta = self._meta(td)
            self.assertEqual(meta["repos"], 3)
            self.assertEqual(meta["retired"], 1)

    def test_registry_missing_counts_all_clones(self):
        with tempfile.TemporaryDirectory() as td:
            eco = Path(td)
            (eco / "whatever" / ".git").mkdir(parents=True)
            meta = self._meta(td)
            self.assertEqual(meta["repos"], 1)
            self.assertNotIn("retired", meta)

    def test_registry_env_override(self):
        with tempfile.TemporaryDirectory() as td:
            eco = Path(td)
            (eco / "kept" / ".git").mkdir(parents=True)
            (eco / "gone" / ".git").mkdir(parents=True)
            reg = Path(td) / "custom.json"
            reg.write_text(json.dumps({"repositories": [{"name": "kept"}]}))
            with patch.dict(os.environ, {
                    "OFFICE_ECOSYSTEM_DIR": td,
                    "OFFICE_REGISTRY_JSON": str(reg)}):
                meta = ecomon._meta()
            self.assertEqual(meta["repos"], 1)
            self.assertEqual(meta["retired"], 1)


if __name__ == "__main__":
    unittest.main()
