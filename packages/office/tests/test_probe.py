import unittest

from devin_office.probe import executor_spawn_kwargs


class ProbePlatformTests(unittest.TestCase):
    def test_windows_uses_windows_creation_flags(self):
        options = executor_spawn_kwargs(platform="nt")
        self.assertIn("creationflags", options)
        self.assertNotIn("start_new_session", options)

    def test_posix_uses_session_detachment_not_windows_flags(self):
        options = executor_spawn_kwargs(platform="posix")
        self.assertNotIn("creationflags", options)
        self.assertTrue(options["start_new_session"])


if __name__ == "__main__":
    unittest.main()
