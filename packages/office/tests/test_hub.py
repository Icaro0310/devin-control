import json
import threading
import unittest
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from unittest.mock import patch

from devin_office import hub


def _eco_upstream(payload: dict) -> ThreadingHTTPServer:
    """HTTP server that serves a fixed eco payload — stands in for the
    office daemon's /api/eco-raw behind OFFICE_ECO_URL."""
    body = json.dumps(payload).encode()

    class _Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args):
            pass

    return ThreadingHTTPServer(("127.0.0.1", 0), _Handler)


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

    def test_ecosystem_proxy_returns_upstream_data(self):
        """DL-17a7: with OFFICE_ECO_URL set, GET /api/eco must proxy the
        upstream eco payload so the side panel renders real data."""
        upstream_payload = {
            "office-laptop": {
                "probes": {"mcp-hub": 200, "vm-office": 200},
                "tools": {"unified-mcp": True, "slack-poll": False},
                "units": {"devin-janitor.timer": "active"},
                "meta": {"repos": 10, "clis": 42},
            }
        }
        upstream = _eco_upstream(upstream_payload)
        upstream_thread = threading.Thread(
            target=upstream.serve_forever, daemon=True)
        upstream_thread.start()
        server = ThreadingHTTPServer(("127.0.0.1", 0), hub.Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        eco_url = f"http://127.0.0.1:{upstream.server_port}/api/eco-raw"
        try:
            with patch.object(hub, "ECO_URL", eco_url), patch.object(
                hub, "TOKEN", ""
            ), patch.object(
                hub, "_eco_cache", {"at": 0.0, "data": None}
            ), patch.object(
                hub, "current_state", return_value={"agents": []}
            ):
                thread.start()
                base = f"http://127.0.0.1:{server.server_port}"
                with urllib.request.urlopen(base + "/api/eco") as resp:
                    data = json.loads(resp.read().decode())
                self.assertEqual(data["eco"], upstream_payload)
                self.assertEqual(data["office"], {"agents": []})
        finally:
            server.shutdown()
            server.server_close()
            thread.join()
            upstream.shutdown()
            upstream.server_close()
            upstream_thread.join()

    def test_ecosystem_proxy_reports_unreachable_upstream(self):
        with patch.object(hub, "ECO_URL", "http://127.0.0.1:9/eco"), patch.object(
            hub, "_eco_cache", {"at": 0.0, "data": None}
        ), patch.object(
            hub, "current_state", return_value={"agents": []}
        ), patch(
            "urllib.request.urlopen", side_effect=OSError("refused")
        ):
            result = hub.eco_status()
        self.assertEqual(result["eco"],
                         {"error": "ecosystem API unreachable"})
        self.assertEqual(result["office"], {"agents": []})


if __name__ == "__main__":
    unittest.main()
