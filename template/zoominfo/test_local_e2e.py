import threading
import unittest

from local_e2e import FOUNDRY_CALLER_TOKEN, MODES, LocalGatewayServer, _authorize_and_exchange, _mcp_call, run_demo
from mock_service import LocalOnlyServer


class LocalEndToEndTests(unittest.TestCase):
    def test_both_auth_ownership_routes_reach_lookup(self):
        for mode in sorted(MODES):
            with self.subTest(mode=mode):
                result = run_demo(mode)
                self.assertEqual(result["result"], "PASS")
                self.assertFalse(result["token_values_printed"])
                self.assertEqual(result["lookup_fixture"], "fictional local-mock data")

    def test_apim_broker_ignores_client_supplied_backend_token(self):
        upstream = LocalOnlyServer(0)
        upstream_thread = threading.Thread(target=upstream.serve_forever, daemon=True)
        upstream_thread.start()
        try:
            gateway = LocalGatewayServer(
                0,
                f"http://127.0.0.1:{upstream.server_port}/mcp",
                "apim_credential_manager",
                broker_token="invalid-not-issued",
            )
            gateway_thread = threading.Thread(target=gateway.serve_forever, daemon=True)
            gateway_thread.start()
            try:
                status, response = _mcp_call(
                    f"http://127.0.0.1:{gateway.server_port}",
                    "apim_credential_manager",
                    1,
                    "tools/list",
                    None,
                    extra_headers={
                        "X-Foundry-User-Authorization": "Bearer valid-client-supplied-token-must-be-ignored"
                    },
                )
                self.assertEqual(status, 401)
                self.assertEqual(response["error"], "unauthorized")
            finally:
                gateway.shutdown()
                gateway.server_close()
                gateway_thread.join(timeout=2)
        finally:
            upstream.shutdown()
            upstream.server_close()
            upstream_thread.join(timeout=2)

    def test_gateway_rejects_unauthorized_caller_before_forwarding(self):
        upstream = LocalOnlyServer(0)
        upstream_thread = threading.Thread(target=upstream.serve_forever, daemon=True)
        upstream_thread.start()
        try:
            valid_user_token = _authorize_and_exchange(
                f"http://127.0.0.1:{upstream.server_port}"
            )
            gateway = LocalGatewayServer(
                0,
                f"http://127.0.0.1:{upstream.server_port}/mcp",
                "apim_credential_manager",
                broker_token=valid_user_token,
            )
            gateway_thread = threading.Thread(target=gateway.serve_forever, daemon=True)
            gateway_thread.start()
            try:
                status, response = _mcp_call(
                    f"http://127.0.0.1:{gateway.server_port}",
                    "apim_credential_manager",
                    2,
                    "tools/list",
                    None,
                    caller_token="not-the-local-foundry-caller",
                )
                self.assertEqual(status, 401)
                self.assertEqual(response["error"], "unauthorized_local_foundry_caller")
            finally:
                gateway.shutdown()
                gateway.server_close()
                gateway_thread.join(timeout=2)
        finally:
            upstream.shutdown()
            upstream.server_close()
            upstream_thread.join(timeout=2)

    def test_gateway_rejects_non_read_only_tool_method(self):
        upstream = LocalOnlyServer(0)
        upstream_thread = threading.Thread(target=upstream.serve_forever, daemon=True)
        upstream_thread.start()
        try:
            valid_user_token = _authorize_and_exchange(
                f"http://127.0.0.1:{upstream.server_port}"
            )
            gateway = LocalGatewayServer(
                0,
                f"http://127.0.0.1:{upstream.server_port}/mcp",
                "apim_credential_manager",
                broker_token=valid_user_token,
            )
            gateway_thread = threading.Thread(target=gateway.serve_forever, daemon=True)
            gateway_thread.start()
            try:
                status, response = _mcp_call(
                    f"http://127.0.0.1:{gateway.server_port}",
                    "apim_credential_manager",
                    3,
                    "tools/call",
                    {"name": "Delete", "arguments": {}},
                )
                self.assertEqual(status, 403)
                self.assertEqual(response["error"], "only_read_only_lookup_mcp_methods_allowed")
            finally:
                gateway.shutdown()
                gateway.server_close()
                gateway_thread.join(timeout=2)
        finally:
            upstream.shutdown()
            upstream.server_close()
            upstream_thread.join(timeout=2)

    def test_gateway_only_accepts_loopback_upstream(self):
        with self.assertRaisesRegex(ValueError, "loopback HTTP"):
            LocalGatewayServer(
                0,
                "http://example.test/mcp",
                "apim_credential_manager",
                broker_token="synthetic",
            )

    def test_caller_token_fixture_is_explicitly_synthetic(self):
        self.assertIn("mock-foundry-caller", FOUNDRY_CALLER_TOKEN)


if __name__ == "__main__":
    unittest.main()
