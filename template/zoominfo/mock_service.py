#!/usr/bin/env python3
"""Loopback-only mock OAuth PKCE issuer and Streamable HTTP MCP server."""

import argparse
import base64
import hashlib
import json
import re
import secrets
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlencode, urlsplit


MOCK_CLIENT_ID = "zoominfo-local-mock-client"
MOCK_SCOPE = "lookup"
MAX_BODY_BYTES = 64 * 1024
CODE_LIFETIME_SECONDS = 120
TOKEN_LIFETIME_SECONDS = 3600
MAX_AUTHORIZATION_CODES = 128
VERIFIER_PATTERN = re.compile(r"^[A-Za-z0-9._~-]{43,128}$")
MOCK_RESULT = {
    "matches": [
        {
            "id": "mock-contact-001",
            "name": "Taylor Sample",
            "company": "Example Test Company",
            "title": "Mock Validation Contact",
        }
    ],
    "source": "local-mock",
}


def _json_response(handler, status, body, headers=None):
    encoded = json.dumps(body, separators=(",", ":")).encode("utf-8")
    handler.send_response(status)
    handler.send_header("Content-Type", "application/json; charset=utf-8")
    handler.send_header("Content-Length", str(len(encoded)))
    handler.send_header("Cache-Control", "no-store")
    handler.send_header("Pragma", "no-cache")
    for name, value in (headers or {}).items():
        handler.send_header(name, value)
    handler.end_headers()
    handler.wfile.write(encoded)


def _oauth_error(handler, status, error):
    _json_response(handler, status, {"error": error})


def _form_value(values, name):
    found = values.get(name, [])
    return found[0] if len(found) == 1 else None


def _read_body(handler):
    length_value = handler.headers.get("Content-Length", "")
    if not length_value.isdigit():
        return None
    length = int(length_value)
    if length > MAX_BODY_BYTES:
        return None
    return handler.rfile.read(length)


def make_handler(redirect_uri):
    codes = {}
    access_tokens = {}
    state_lock = threading.Lock()

    class MockHandler(BaseHTTPRequestHandler):
        server_version = "LocalMock/1.0"
        sys_version = ""

        def log_message(self, _format, *_args):
            return

        def do_GET(self):
            parsed = urlsplit(self.path)
            if parsed.path == "/healthz":
                _json_response(self, 200, {"status": "ok", "service": "local-mock-oauth-mcp"})
                return
            if parsed.path == "/oauth/authorize":
                self._authorize(parsed.query)
                return
            if parsed.path == "/callback":
                query = parse_qs(parsed.query, strict_parsing=True)
                code = _form_value(query, "code")
                state = _form_value(query, "state")
                if code is None or state is None:
                    _oauth_error(self, 400, "invalid_callback")
                    return
                _json_response(self, 200, {"code": code, "state": state, "notice": "synthetic local test code"})
                return
            _oauth_error(self, 404, "not_found")

        def do_POST(self):
            if self.path not in {"/oauth/token", "/mcp"}:
                _oauth_error(self, 404, "not_found")
                return
            body = _read_body(self)
            if body is None:
                _oauth_error(self, 413, "request_too_large_or_invalid_length")
                return
            if self.path == "/oauth/token":
                self._token(body)
            else:
                self._mcp(body)

        def _authorize(self, raw_query):
            try:
                query = parse_qs(raw_query, strict_parsing=True)
            except ValueError:
                _oauth_error(self, 400, "invalid_request")
                return
            response_type = _form_value(query, "response_type")
            client_id = _form_value(query, "client_id")
            callback = _form_value(query, "redirect_uri")
            scope = _form_value(query, "scope")
            state = _form_value(query, "state")
            challenge = _form_value(query, "code_challenge")
            method = _form_value(query, "code_challenge_method")
            if (
                response_type != "code"
                or client_id != MOCK_CLIENT_ID
                or callback != redirect_uri
                or scope != MOCK_SCOPE
                or not state
                or len(state) > 512
                or not challenge
                or len(challenge) > 128
                or method != "S256"
            ):
                _oauth_error(self, 400, "invalid_request_pkce_s256_and_local_client_required")
                return
            code = secrets.token_urlsafe(24)
            with state_lock:
                expired_codes = [value for value, grant in codes.items() if grant[2] < time.monotonic()]
                for expired_code in expired_codes:
                    codes.pop(expired_code, None)
                if len(codes) >= MAX_AUTHORIZATION_CODES:
                    _oauth_error(self, 429, "too_many_local_authorization_attempts")
                    return
                codes[code] = (challenge, callback, time.monotonic() + CODE_LIFETIME_SECONDS)
            location = callback + "?" + urlencode({"code": code, "state": state})
            self.send_response(302)
            self.send_header("Location", location)
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", "0")
            self.end_headers()

        def _token(self, body):
            if self.headers.get_content_type() != "application/x-www-form-urlencoded":
                _oauth_error(self, 415, "unsupported_media_type")
                return
            try:
                form = parse_qs(body.decode("utf-8"), strict_parsing=True)
            except (UnicodeDecodeError, ValueError):
                _oauth_error(self, 400, "invalid_request")
                return
            grant_type = _form_value(form, "grant_type")
            client_id = _form_value(form, "client_id")
            code = _form_value(form, "code")
            callback = _form_value(form, "redirect_uri")
            verifier = _form_value(form, "code_verifier")
            if (
                grant_type != "authorization_code"
                or client_id != MOCK_CLIENT_ID
                or not code
                or callback != redirect_uri
                or not verifier
                or not VERIFIER_PATTERN.fullmatch(verifier)
            ):
                _oauth_error(self, 400, "invalid_grant")
                return

            with state_lock:
                grant = codes.pop(code, None)
                if grant is None:
                    _oauth_error(self, 400, "invalid_grant")
                    return
                challenge, saved_callback, expires = grant
                actual_challenge = base64.urlsafe_b64encode(
                    hashlib.sha256(verifier.encode("ascii")).digest()
                ).rstrip(b"=").decode("ascii")
                if saved_callback != callback or expires < time.monotonic() or not secrets.compare_digest(challenge, actual_challenge):
                    _oauth_error(self, 400, "invalid_grant")
                    return
                token = "mock_" + secrets.token_urlsafe(32)
                access_tokens[token] = time.monotonic() + TOKEN_LIFETIME_SECONDS
            _json_response(
                self,
                200,
                {
                    "access_token": token,
                    "token_type": "Bearer",
                    "expires_in": TOKEN_LIFETIME_SECONDS,
                    "scope": MOCK_SCOPE,
                    "sub": "synthetic-user-001",
                },
            )

        def _mcp(self, body):
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
            auth = self.headers.get("Authorization", "")
            scheme, _, token = auth.partition(" ")
            with state_lock:
                expires = access_tokens.get(token)
                if expires is not None and expires < time.monotonic():
                    access_tokens.pop(token, None)
                    expires = None
                authorized = scheme.lower() == "bearer" and expires is not None
            if not authorized:
                _json_response(
                    self,
                    401,
                    {"error": "unauthorized"},
                    {"WWW-Authenticate": 'Bearer realm="local-mock-mcp"'},
                )
                return
            try:
                request = json.loads(body.decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError):
                _mcp_error(self, None, -32700, "Parse error")
                return
            if not isinstance(request, dict) or request.get("jsonrpc") != "2.0":
                _mcp_error(self, None, -32600, "Invalid Request")
                return
            method = request.get("method")
            request_id = request.get("id")
            if method == "notifications/initialized" and "id" not in request:
                self.send_response(202)
                self.send_header("Content-Length", "0")
                self.end_headers()
                return
            if "id" not in request:
                self.send_response(202)
                self.send_header("Content-Length", "0")
                self.end_headers()
                return
            if method == "initialize":
                result = {
                    "protocolVersion": "2025-03-26",
                    "capabilities": {"tools": {}},
                    "serverInfo": {"name": "local-zoominfo-mock", "version": "1.0.0"},
                }
                _mcp_result(self, request_id, result)
                return
            if method == "ping":
                _mcp_result(self, request_id, {})
                return
            if method == "tools/list":
                _mcp_result(
                    self,
                    request_id,
                    {
                        "tools": [
                            {
                                "name": "Lookup",
                                "description": "Read-only lookup against fictional local fixture data.",
                                "inputSchema": {
                                    "type": "object",
                                    "properties": {
                                        "query": {"type": "string", "minLength": 1, "maxLength": 200},
                                        "limit": {"type": "integer", "minimum": 1, "maximum": 5},
                                    },
                                    "required": ["query"],
                                    "additionalProperties": False,
                                },
                                "annotations": {"readOnlyHint": True, "destructiveHint": False},
                            }
                        ]
                    },
                )
                return
            if method == "tools/call":
                self._call_tool(request_id, request.get("params"))
                return
            _mcp_error(self, request_id, -32601, "Method not found")

        def _call_tool(self, request_id, params):
            if not isinstance(params, dict) or params.get("name") != "Lookup":
                _mcp_error(self, request_id, -32602, "Only the read-only Lookup tool is available")
                return
            arguments = params.get("arguments")
            if not isinstance(arguments, dict):
                _mcp_error(self, request_id, -32602, "Lookup arguments must be an object")
                return
            query = arguments.get("query")
            limit = arguments.get("limit", 1)
            if (
                not isinstance(query, str)
                or not query.strip()
                or len(query) > 200
                or not isinstance(limit, int)
                or isinstance(limit, bool)
                or not 1 <= limit <= 5
                or set(arguments) - {"query", "limit"}
            ):
                _mcp_error(self, request_id, -32602, "Invalid Lookup arguments")
                return
            _mcp_result(
                self,
                request_id,
                {
                    "content": [{"type": "text", "text": json.dumps(MOCK_RESULT, separators=(",", ":"))}],
                    "structuredContent": MOCK_RESULT,
                    "isError": False,
                },
            )

    return MockHandler


def _mcp_error(handler, request_id, code, message):
    _json_response(
        handler,
        200,
        {"jsonrpc": "2.0", "id": request_id, "error": {"code": code, "message": message}},
    )


def _mcp_result(handler, request_id, result):
    _json_response(handler, 200, {"jsonrpc": "2.0", "id": request_id, "result": result})


class LocalOnlyServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True

    def __init__(self, port):
        super().__init__(("127.0.0.1", port), BaseHTTPRequestHandler)
        self.RequestHandlerClass = make_handler(f"http://127.0.0.1:{self.server_port}/callback")


def main():
    parser = argparse.ArgumentParser(description="Start the loopback-only mock OAuth PKCE issuer and MCP Lookup server.")
    parser.add_argument("--port", type=int, default=8765, help="loopback port (default: 8765)")
    args = parser.parse_args()
    if not 0 <= args.port <= 65535:
        parser.error("--port must be between 0 and 65535")
    server = LocalOnlyServer(args.port)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    print(f"Local mock listening at http://127.0.0.1:{server.server_port} (loopback only; Ctrl+C to stop)", flush=True)
    try:
        thread.join()
    except KeyboardInterrupt:
        pass
    finally:
        server.shutdown()
        server.server_close()


if __name__ == "__main__":
    main()
