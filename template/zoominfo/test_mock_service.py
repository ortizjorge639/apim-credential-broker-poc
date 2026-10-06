import base64
import hashlib
import http.client
import json
import threading
import unittest
from urllib.parse import parse_qs, urlencode, urlsplit

from mock_service import MOCK_CLIENT_ID, LocalOnlyServer


class MockServiceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = LocalOnlyServer(0)
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        cls.port = cls.server.server_port
        cls.base_url = f"http://127.0.0.1:{cls.port}"
        cls.redirect_uri = f"{cls.base_url}/callback"

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join(timeout=2)

    def request(self, method, path, body=None, headers=None):
        connection = http.client.HTTPConnection("127.0.0.1", self.port, timeout=2)
        connection.request(method, path, body=body, headers=headers or {})
        response = connection.getresponse()
        content = response.read()
        result = (response.status, dict(response.getheaders()), content)
        connection.close()
        return result

    def authorize(self, verifier="A" * 43, **overrides):
        challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode("ascii")).digest()).rstrip(b"=").decode("ascii")
        params = {
            "response_type": "code",
            "client_id": MOCK_CLIENT_ID,
            "redirect_uri": self.redirect_uri,
            "scope": "lookup",
            "state": "local-test-state",
            "code_challenge": challenge,
            "code_challenge_method": "S256",
        }
        params.update(overrides)
        status, headers, _ = self.request("GET", "/oauth/authorize?" + urlencode(params))
        return status, headers, verifier

    def exchange(self, code, verifier="A" * 43, redirect_uri=None):
        data = urlencode({
            "grant_type": "authorization_code",
            "client_id": MOCK_CLIENT_ID,
            "code": code,
            "redirect_uri": redirect_uri or self.redirect_uri,
            "code_verifier": verifier,
        })
        return self.request(
            "POST",
            "/oauth/token",
            data,
            {"Content-Type": "application/x-www-form-urlencoded"},
        )

    def get_code(self, headers):
        location = headers["Location"]
        callback = urlsplit(location)
        status, _, body = self.request("GET", callback.path + "?" + callback.query)
        self.assertEqual(status, 200)
        return json.loads(body)["code"]

    def mcp(self, token, payload):
        headers = {"Content-Type": "application/json", "Accept": "application/json, text/event-stream"}
        if token:
            headers["Authorization"] = "Bearer " + token
        return self.request("POST", "/mcp", json.dumps(payload), headers)

    def access_token(self):
        status, headers, verifier = self.authorize()
        self.assertEqual(status, 302)
        code = self.get_code(headers)
        status, _, body = self.exchange(code, verifier)
        self.assertEqual(status, 200)
        return json.loads(body)["access_token"]

    def test_health_endpoint_is_loopback_service(self):
        status, _, body = self.request("GET", "/healthz")
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(body)["service"], "local-mock-oauth-mcp")
        self.assertEqual(self.server.server_address[0], "127.0.0.1")

    def test_authorization_requires_pkce_s256(self):
        status, _, body = self.request(
            "GET",
            "/oauth/authorize?" + urlencode({
                "response_type": "code",
                "client_id": MOCK_CLIENT_ID,
                "redirect_uri": self.redirect_uri,
                "scope": "lookup",
                "state": "state",
                "code_challenge": "some-challenge",
                "code_challenge_method": "plain",
            }),
        )
        self.assertEqual(status, 400)
        self.assertEqual(json.loads(body)["error"], "invalid_request_pkce_s256_and_local_client_required")

    def test_authorization_code_pkce_exchange_and_replay_protection(self):
        status, headers, verifier = self.authorize()
        self.assertEqual(status, 302)
        callback = urlsplit(headers["Location"])
        callback_params = parse_qs(callback.query)
        self.assertEqual(callback_params["state"], ["local-test-state"])
        code = self.get_code(headers)

        status, _, body = self.exchange(code, verifier)
        self.assertEqual(status, 200)
        token = json.loads(body)["access_token"]
        self.assertTrue(token.startswith("mock_"))
        status, _, replay = self.exchange(code, verifier)
        self.assertEqual(status, 400)
        self.assertEqual(json.loads(replay)["error"], "invalid_grant")

    def test_wrong_verifier_and_redirect_are_rejected(self):
        status, headers, _ = self.authorize()
        code = self.get_code(headers)
        status, _, body = self.exchange(code, "B" * 43)
        self.assertEqual(status, 400)
        self.assertEqual(json.loads(body)["error"], "invalid_grant")

        status, headers, verifier = self.authorize()
        code = self.get_code(headers)
        status, _, body = self.exchange(code, verifier, "http://127.0.0.1:9999/callback")
        self.assertEqual(status, 400)
        self.assertEqual(json.loads(body)["error"], "invalid_grant")

    def test_mcp_requires_issued_bearer_token(self):
        status, headers, body = self.mcp(None, {"jsonrpc": "2.0", "id": 1, "method": "tools/list"})
        self.assertEqual(status, 401)
        self.assertIn("Bearer", headers["WWW-Authenticate"])
        self.assertEqual(json.loads(body)["error"], "unauthorized")

    def test_mcp_requires_streamable_http_accept_types(self):
        status, _, body = self.request(
            "POST",
            "/mcp",
            json.dumps({"jsonrpc": "2.0", "id": 1, "method": "initialize"}),
            {"Content-Type": "application/json", "Accept": "application/json", "Authorization": "Bearer invalid"},
        )
        self.assertEqual(status, 406)
        self.assertEqual(json.loads(body)["error"], "accept_must_include_application_json_and_text_event_stream")

    def test_streamable_http_initialize_list_and_synthetic_lookup(self):
        token = self.access_token()
        status, headers, body = self.mcp(token, {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "2025-03-26"}})
        self.assertEqual(status, 200)
        self.assertEqual(headers["Content-Type"], "application/json; charset=utf-8")
        self.assertEqual(json.loads(body)["result"]["serverInfo"]["name"], "local-zoominfo-mock")

        status, _, body = self.mcp(token, {"jsonrpc": "2.0", "id": 2, "method": "tools/list"})
        tool = json.loads(body)["result"]["tools"][0]
        self.assertEqual(tool["name"], "Lookup")
        self.assertTrue(tool["annotations"]["readOnlyHint"])

        status, _, body = self.mcp(token, {
            "jsonrpc": "2.0",
            "id": 3,
            "method": "tools/call",
            "params": {"name": "Lookup", "arguments": {"query": "mock only"}},
        })
        result = json.loads(body)["result"]
        self.assertFalse(result["isError"])
        self.assertEqual(result["structuredContent"]["source"], "local-mock")
        self.assertEqual(result["structuredContent"]["matches"][0]["company"], "Example Test Company")

    def test_no_write_tool_and_invalid_lookup_arguments(self):
        token = self.access_token()
        status, _, body = self.mcp(token, {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {"name": "Update", "arguments": {}},
        })
        self.assertIn("error", json.loads(body))

        status, _, body = self.mcp(token, {
            "jsonrpc": "2.0",
            "id": 2,
            "method": "tools/call",
            "params": {"name": "Lookup", "arguments": {"query": "x", "delete": True}},
        })
        self.assertIn("error", json.loads(body))

    def test_notification_has_empty_accepted_response(self):
        token = self.access_token()
        status, _, body = self.mcp(token, {"jsonrpc": "2.0", "method": "notifications/initialized"})
        self.assertEqual(status, 202)
        self.assertEqual(body, b"")


if __name__ == "__main__":
    unittest.main()
