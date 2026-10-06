import copy
import unittest

from zoominfo_validation import evaluate, validate_config


def valid_config(mode):
    return {
        "app_type": "Standard",
        "authorization_mode": mode,
        "client_id": "customer-client-id",
        "authorization_url": "https://auth.example.test/authorize",
        "token_url": "https://auth.example.test/token",
        "refresh_url": "https://auth.example.test/refresh",
        "redirect_url": "https://apim.example.test/redirect",
        "scopes": ["customer-approved-scope"],
        "apim_mcp_endpoint": "https://apim.example.test/zoominfo/mcp",
        "lookup_tool": "Lookup",
        "lookup_arguments": {"query": "approved test input"},
    }


def valid_evidence():
    return {
        "authorization_succeeded": True,
        "token_exchange_succeeded": True,
        "pkce_s256_verified": True,
        "lookup_read_only_verified": True,
        "lookup_http_status": 200,
        "lookup_jsonrpc_error": False,
        "lookup_mcp_is_error": False,
        "per_user_delegation_verified": True,
        "evidence_refs": ["redacted-run-record"],
    }


class ZoomInfoValidationTests(unittest.TestCase):
    def test_rejects_non_standard_app_or_non_lookup_tool(self):
        config = valid_config("foundry_oauth_passthrough")
        config["app_type"] = "Unknown"
        config["lookup_tool"] = "Update"
        errors = validate_config(config)
        self.assertIn("app_type must be Standard", errors)
        self.assertIn("only the read-only Lookup tool is allowed", errors)

    def test_rejects_http_urls_and_empty_scopes(self):
        config = valid_config("apim_credential_manager")
        config["token_url"] = "http://auth.example.test/token"
        config["scopes"] = []
        errors = validate_config(config)
        self.assertTrue(any("token_url must be an absolute HTTPS URL" in error for error in errors))
        self.assertIn("scopes must be a non-empty array of customer-approved scope strings", errors)

    def test_foundry_go_requires_delegated_identity_and_lookup(self):
        config = valid_config("foundry_oauth_passthrough")
        evidence = valid_evidence()
        self.assertEqual(evaluate(config, evidence)["decision"], "GO")
        evidence["per_user_delegation_verified"] = False
        result = evaluate(config, evidence)
        self.assertEqual(result["decision"], "PARTIAL")
        self.assertTrue(any("per-user delegated identity" in reason for reason in result["reasons"]))

    def test_token_exchange_alone_is_partial(self):
        config = valid_config("apim_credential_manager")
        evidence = valid_evidence()
        evidence["lookup_http_status"] = None
        evidence["pkce_s256_verified"] = False
        result = evaluate(config, evidence)
        self.assertEqual(result["decision"], "PARTIAL")
        self.assertTrue(any("token exchange alone is partial" in reason for reason in result["reasons"]))

    def test_apim_go_requires_s256_and_lookup(self):
        config = valid_config("apim_credential_manager")
        evidence = valid_evidence()
        evidence["pkce_s256_verified"] = False
        result = evaluate(config, evidence)
        self.assertEqual(result["decision"], "PARTIAL")
        self.assertTrue(any("PKCE S256" in reason for reason in result["reasons"]))

    def test_http_success_without_read_only_proof_stops(self):
        config = valid_config("foundry_oauth_passthrough")
        evidence = valid_evidence()
        evidence["lookup_read_only_verified"] = False
        self.assertEqual(evaluate(config, evidence)["decision"], "STOP")

    def test_apim_pass_requires_token_exchange(self):
        config = valid_config("apim_credential_manager")
        evidence = valid_evidence()
        evidence["token_exchange_succeeded"] = False
        result = evaluate(config, evidence)
        self.assertEqual(result["decision"], "PARTIAL")
        self.assertTrue(any("token exchange has not been proven" in reason for reason in result["reasons"]))

    def test_lookup_errors_stop(self):
        config = valid_config("foundry_oauth_passthrough")
        evidence = copy.deepcopy(valid_evidence())
        evidence["lookup_mcp_is_error"] = True
        result = evaluate(config, evidence)
        self.assertEqual(result["decision"], "STOP")
        self.assertTrue(any("MCP/JSON-RPC error" in reason for reason in result["reasons"]))

    def test_rejects_secrets_in_config_and_evidence_refs(self):
        config = valid_config("apim_credential_manager")
        config["client_secret"] = "must-not-be-here"
        self.assertTrue(any("unsupported config keys" in error for error in validate_config(config)))
        evidence = valid_evidence()
        evidence["evidence_refs"] = ["Bearer abc"]
        self.assertEqual(evaluate(valid_config("apim_credential_manager"), evidence)["decision"], "STOP")


if __name__ == "__main__":
    unittest.main()
