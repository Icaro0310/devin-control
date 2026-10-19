import json
import threading
import unittest
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from unittest.mock import patch

from devin_office import hub


class HubSecurityTests(unittest.TestCase):
    def test_loopback_can_run_without_a_token(self):
        hub.validate_binding("127.0.0.1", "")
        hub.validate_binding("::1", "")

    def test_non_loopback_bind_requires_a_token(self):
        with self.assertRaisesRegex(ValueError, "OFFICE_TOKEN is required"):
            hub.validate_binding("0.0.0.0", "")

    def test_non_loopback_bind_accepts_a_configured_token(self):
        hub.validate_binding("0.0.0.0", "configured-token")

    def test_command_endpoints_are_disabled_by_default(self):
        server = ThreadingHTTPServer(("127.0.0.1", 0), hub.Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        with patch.object(hub, "CONTROL_ENABLED", False), patch.object(hub, "TOKEN", ""):
            thread.start()
            base = f"http://127.0.0.1:{server.server_port}"
            try:
                with self.assertRaises(urllib.error.HTTPError) as pending:
                    urllib.request.urlopen(base + "/api/cmd/pending")
                self.assertEqual(pending.exception.code, 403)
                pending.exception.close()
                request = urllib.request.Request(
                    base + "/api/cmd",
                    data=json.dumps({"action": "spawn", "text": "test"}).encode(),
                    headers={"Content-Type": "application/json"},
                    method="POST",
                )
                with self.assertRaises(urllib.error.HTTPError) as command:
                    urllib.request.urlopen(request)
                self.assertEqual(command.exception.code, 403)
                command.exception.close()
            finally:
                server.shutdown()
                server.server_close()
                thread.join()

    def test_ecosystem_proxy_is_disabled_by_default(self):
        with patch.object(hub, "ECO_URL", ""), patch.object(
            hub, "current_state", return_value={"agents": []}
        ):
            result = hub.eco_status()
        self.assertIsNone(result["eco"])
        self.assertEqual(result["office"], {"agents": []})


if __name__ == "__main__":
    unittest.main()
