#!/usr/bin/env python3
"""Run a dependency-free Foundry/APIM-shaped OAuth-to-MCP flow on loopback."""

import argparse
import base64
import hashlib
import http.client
import json
import secrets
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlencode, urlsplit

from mock_service import LocalOnlyServer, MOCK_CLIENT_ID, MOCK_SCOPE


FOUNDRY_CALLER_TOKEN = "mock-foundry-caller-token-not-a-real-credential"
MODES = {"foundry_oauth_passthrough", "apim_credential_manager"}
MAX_BODY_BYTES = 64 * 1024
UPSTREAM_TIMEOUT_SECONDS = 3


def _json_response(handler, status, body):
    encoded = json.dumps(body, separators=(",", ":")).encode("utf-8")
    handler.send_response(status)
    handler.send_header("Content-Type", "application/json; charset=utf-8")
    handler.send_header("Content-Length", str(len(encoded)))
    handler.send_header("Cache-Control", "no-store")
    handler.end_headers()
    handler.wfile.write(encoded)


def make_gateway_handler(upstream_url, mode, broker_token):
    upstream = urlsplit(upstream_url)
    if (
        upstream.scheme != "http"
        or upstream.hostname != "127.0.0.1"
        or upstream.port is None
        or upstream.username
        or upstream.password
        or upstream.query
        or upstream.fragment
    ):
        raise ValueError("mock gateway upstream must be an explicit loopback HTTP URL")
    if mode not in MODES:
        raise ValueError("unsupported local authentication mode")
    if mode == "apim_credential_manager" and not broker_token:
        raise ValueError("APIM mock mode requires an in-memory broker token")

    class GatewayHandler(BaseHTTPRequestHandler):
        server_version = "LocalAPIMMock/1.0"
        sys_version = ""

        def log_message(self, _format, *_args):
            return

        def do_POST(self):
            if self.path != "/mcp":
                _json_response(self, 404, {"error": "not_found"})
                return
            if not secrets.compare_digest(self.headers.get("Authorization", ""), "Bearer " + FOUNDRY_CALLER_TOKEN):
                _json_response(self, 401, {"error": "unauthorized_local_foundry_caller"})
                return
            if self.headers.get_content_type() != "application/json":
                _json_response(self, 415, {"error": "unsupported_media_type"})
                return
            accepted = {
                media.strip().split(";", 1)[0].lower()
                for media in self.headers.get("Accept", "").split(",")
                if media.strip()
            }
            if not {"application/json", "text/event-stream"}.issubset(accepted):
                _json_response(self, 406, {"error": "accept_must_include_application_json_and_text_event_stream"})
                return
            length = self.headers.get("Content-Length", "")
            if not length.isdigit():
                _json_response(self, 411, {"error": "content_length_required"})
                return
            if int(length) > MAX_BODY_BYTES:
                _json_response(self, 413, {"error": "request_too_large"})
                return
            body = self.rfile.read(int(length))
            try:
                request = json.loads(body.decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError):
                _json_response(self, 400, {"error": "invalid_json"})
                return
            if not _allowed_read_only_mcp(request):
                _json_response(self, 403, {"error": "only_read_only_lookup_mcp_methods_allowed"})
                return

            if mode == "foundry_oauth_passthrough":
                backend_auth = self.headers.get("X-Foundry-User-Authorization", "")
                scheme, _, user_token = backend_auth.partition(" ")
                if scheme.lower() != "bearer" or not user_token:
                    _json_response(self, 401, {"error": "missing_delegated_user_token"})
                    return
                backend_token = user_token
            else:
                backend_token = broker_token

            connection = http.client.HTTPConnection(upstream.hostname, upstream.port, timeout=UPSTREAM_TIMEOUT_SECONDS)
            try:
                connection.request(
                    "POST",
                    upstream.path or "/mcp",
                    body=body,
                    headers={
                        "Authorization": "Bearer " + backend_token,
                        "Content-Type": "application/json",
                        "Accept": "application/json, text/event-stream",
                    },
                )
                response = connection.getresponse()
                response_body = response.read(MAX_BODY_BYTES + 1)
                if len(response_body) > MAX_BODY_BYTES:
                    _json_response(self, 502, {"error": "mock_upstream_response_too_large"})
                    return
                self.send_response(response.status)
                content_type = response.getheader("Content-Type", "application/json")
                self.send_header("Content-Type", content_type)
                self.send_header("Content-Length", str(len(response_body)))
                self.send_header("Cache-Control", "no-store")
                upstream_session_id = response.getheader("Mcp-Session-Id")
                if upstream_session_id:
                    self.send_header("Mcp-Session-Id", upstream_session_id)
                self.end_headers()
                self.wfile.write(response_body)
            except (OSError, http.client.HTTPException):
                _json_response(self, 502, {"error": "mock_upstream_unavailable"})
            finally:
                connection.close()

    return GatewayHandler


def _allowed_read_only_mcp(request):
    if not isinstance(request, dict) or request.get("jsonrpc") != "2.0":
        return False
    method = request.get("method")
    if method == "notifications/initialized":
        return "id" not in request
    if "id" not in request:
        return False
    if method in {"initialize", "ping", "tools/list"}:
        return True
    if method != "tools/call":
        return False
    params = request.get("params")
    return isinstance(params, dict) and params.get("name") == "Lookup"


class LocalGatewayServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True

    def __init__(self, port, upstream_url, mode, broker_token=None):
        handler = make_gateway_handler(upstream_url, mode, broker_token)
        super().__init__(("127.0.0.1", port), BaseHTTPRequestHandler)
        self.RequestHandlerClass = handler


def _request(base_url, method, path, body=None, headers=None):
    parsed = urlsplit(base_url)
    connection = http.client.HTTPConnection(parsed.hostname, parsed.port, timeout=UPSTREAM_TIMEOUT_SECONDS)
    try:
        connection.request(method, path, body=body, headers=headers or {})
        response = connection.getresponse()
        response_body = response.read(MAX_BODY_BYTES + 1)
        if len(response_body) > MAX_BODY_BYTES:
            raise RuntimeError("local response exceeded the size limit")
        return response.status, dict(response.getheaders()), response_body
    except (OSError, http.client.HTTPException) as error:
        raise RuntimeError("local mock request failed") from error
    finally:
        connection.close()


def _authorize_and_exchange(issuer_url):
    parsed = urlsplit(issuer_url)
    callback = f"http://127.0.0.1:{parsed.port}/callback"
    verifier = secrets.token_urlsafe(48)
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode("ascii")).digest()).rstrip(b"=").decode("ascii")
    state = secrets.token_urlsafe(24)
    authorization = urlencode({
        "response_type": "code",
        "client_id": MOCK_CLIENT_ID,
        "redirect_uri": callback,
        "scope": MOCK_SCOPE,
        "state": state,
        "code_challenge": challenge,
        "code_challenge_method": "S256",
    })
    status, headers, _ = _request(issuer_url, "GET", "/oauth/authorize?" + authorization)
    if status != 302:
        raise RuntimeError(f"mock OAuth authorization failed (HTTP {status})")
    location = urlsplit(headers.get("Location", ""))
    callback_params = parse_qs(location.query, strict_parsing=True)
    codes = callback_params.get("code", [])
    states = callback_params.get("state", [])
    if location.scheme != "http" or location.hostname != "127.0.0.1" or location.port != parsed.port or len(codes) != 1 or states != [state]:
        raise RuntimeError("mock OAuth callback state/target validation failed")
    form = urlencode({
        "grant_type": "authorization_code",
        "client_id": MOCK_CLIENT_ID,
        "redirect_uri": callback,
        "code": codes[0],
        "code_verifier": verifier,
    })
    status, _, body = _request(
        issuer_url,
        "POST",
        "/oauth/token",
        form,
        {"Content-Type": "application/x-www-form-urlencoded"},
    )
    if status != 200:
        raise RuntimeError(f"mock OAuth token exchange failed (HTTP {status})")
    try:
        token_response = json.loads(body)
        token = token_response["access_token"]
    except (json.JSONDecodeError, KeyError, TypeError) as error:
        raise RuntimeError("mock OAuth token response was invalid") from error
    if not isinstance(token, str) or not token.startswith("mock_"):
        raise RuntimeError("mock OAuth returned an unexpected synthetic token")
    return token


def _mcp_call(
    gateway_url,
    mode,
    request_id,
    method,
    params,
    user_token=None,
    caller_token=FOUNDRY_CALLER_TOKEN,
    extra_headers=None,
):
    request = {"jsonrpc": "2.0", "method": method}
    if request_id is not None:
        request["id"] = request_id
    if params is not None:
        request["params"] = params
    headers = {
        "Authorization": "Bearer " + caller_token,
        "Content-Type": "application/json",
        "Accept": "application/json, text/event-stream",
    }
    if mode == "foundry_oauth_passthrough" and user_token:
        headers["X-Foundry-User-Authorization"] = "Bearer " + user_token
    headers.update(extra_headers or {})
    status, _, body = _request(gateway_url, "POST", "/mcp", json.dumps(request), headers)
    try:
        payload = json.loads(body) if body else {}
    except json.JSONDecodeError as error:
        raise RuntimeError("mock gateway returned invalid JSON") from error
    return status, payload


def run_demo(mode):
    if mode not in MODES:
        raise ValueError("unsupported local authentication mode")
    upstream = LocalOnlyServer(0)
    upstream_thread = threading.Thread(target=upstream.serve_forever, daemon=True)
    upstream_thread.start()
    issuer_url = f"http://127.0.0.1:{upstream.server_port}"
    gateway = None
    gateway_thread = None
    try:
        user_token = _authorize_and_exchange(issuer_url)
        broker_token = user_token if mode == "apim_credential_manager" else None
        gateway = LocalGatewayServer(
            0,
            issuer_url + "/mcp",
            mode,
            broker_token=broker_token,
        )
        gateway_thread = threading.Thread(target=gateway.serve_forever, daemon=True)
        gateway_thread.start()
        gateway_url = f"http://127.0.0.1:{gateway.server_port}"

        steps = []
        for request_id, method, params, expected_status in (
            (1, "initialize", {"protocolVersion": "2025-03-26"}, 200),
            (None, "notifications/initialized", None, 202),
            (2, "tools/list", None, 200),
            (3, "tools/call", {"name": "Lookup", "arguments": {"query": "fictional mock contact"}}, 200),
        ):
            status, response = _mcp_call(gateway_url, mode, request_id, method, params, user_token)
            if status != expected_status or (request_id is not None and response.get("id") != request_id) or "error" in response:
                raise RuntimeError(f"local MCP {method} failed (HTTP {status})")
            steps.append(method)
        result = response.get("result", {})
        if result.get("isError") is not False or result.get("structuredContent", {}).get("source") != "local-mock":
            raise RuntimeError("local Lookup did not return the expected synthetic result")
        return {
            "mode": mode,
            "result": "PASS",
            "steps": ["mock Authorization Code + PKCE S256", "mock token exchange", *steps],
            "lookup_fixture": "fictional local-mock data",
            "token_values_printed": False,
        }
    finally:
        if gateway is not None:
            gateway.shutdown()
            gateway.server_close()
        if gateway_thread is not None:
            gateway_thread.join(timeout=2)
        upstream.shutdown()
        upstream.server_close()
        upstream_thread.join(timeout=2)


def main():
    parser = argparse.ArgumentParser(description="Run a local-only Foundry/APIM-shaped OAuth-to-MCP demonstration.")
    parser.add_argument("--mode", choices=sorted(MODES), default="foundry_oauth_passthrough")
    args = parser.parse_args()
    try:
        print(json.dumps(run_demo(args.mode), indent=2))
    except (RuntimeError, ValueError) as error:
        parser.exit(1, f"Local mock flow failed: {error}\n")


if __name__ == "__main__":
    main()
