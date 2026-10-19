import tempfile
import unittest
from pathlib import Path

from devin_office import swapmon


def _mkproc(root: Path) -> None:
    (root / "meminfo").write_text(
        "MemTotal:        8000000 kB\n"
        "MemAvailable:    2000000 kB\n"
        "SwapTotal:      16000000 kB\n"
        "SwapFree:       12000000 kB\n"
    )
    (root / "vmstat").write_text("pswpin 100\npswpout 200\n")
    for pid, comm, swap, rss, adj in [
        (100, "brave", 524288, 300000, 200),
        (200, "devin-desktop", 262144, 1000000, -1000),
        (300, "idle-app", 0, 50000, 0),
        (400, "kswapd0", 1048576, 0, 0),
    ]:
        d = root / str(pid)
        d.mkdir()
        (d / "comm").write_text(comm + "\n")
        (d / "oom_score_adj").write_text(f"{adj}\n")
        (d / "status").write_text(
            f"Name:\t{comm}\nVmRSS:\t{rss} kB\nVmSwap:\t{swap} kB\n"
        )


class SwapMonTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        _mkproc(self.root)

    def tearDown(self):
        self._tmp.cleanup()

    def test_totals_from_meminfo(self):
        sw = swapmon.collect_swap(self.root)
        self.assertEqual(sw["total_kb"], 16000000)
        self.assertEqual(sw["used_kb"], 4000000)
        self.assertEqual(sw["mem_avail_kb"], 2000000)

    def test_queue_ranked_by_vm_swap_and_skips_zero(self):
        sw = swapmon.collect_swap(self.root)
        self.assertEqual([q["name"] for q in sw["queue"]],
                         ["kswapd0", "brave", "devin-desktop"])
        self.assertNotIn("idle-app", [q["name"] for q in sw["queue"]])

    def test_shielded_flag_uses_oom_score_adj(self):
        sw = swapmon.collect_swap(self.root)
        flags = {q["name"]: q["shielded"] for q in sw["queue"]}
        self.assertTrue(flags["devin-desktop"])
        self.assertFalse(flags["brave"])

    def test_missing_proc_root_returns_none(self):
        self.assertIsNone(swapmon.collect_swap(Path("/nonexistent-proc")))


if __name__ == "__main__":
    unittest.main()
