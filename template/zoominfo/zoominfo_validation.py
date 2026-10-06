#!/usr/bin/env python3
"""Offline config and evidence gate for the ZoomInfo MCP validation plan."""

import argparse
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit


CONFIG_KEYS = {
    "app_type",
    "authorization_mode",
    "client_id",
    "authorization_url",
    "token_url",
    "refresh_url",
    "redirect_url",
    "scopes",
    "apim_mcp_endpoint",
    "lookup_tool",
    "lookup_arguments",
}
EVIDENCE_KEYS = {
    "authorization_succeeded",
    "token_exchange_succeeded",
    "pkce_s256_verified",
    "lookup_read_only_verified",
    "lookup_http_status",
    "lookup_jsonrpc_error",
    "lookup_mcp_is_error",
    "per_user_delegation_verified",
    "evidence_refs",
}
URL_KEYS = (
    "authorization_url",
    "token_url",
    "refresh_url",
    "redirect_url",
    "apim_mcp_endpoint",
)
MODES = {"foundry_oauth_passthrough", "apim_credential_manager"}


def read_json(path):
    try:
        with Path(path).open(encoding="utf-8") as source:
            value = json.load(source)
    except (OSError, json.JSONDecodeError) as error:
        raise ValueError(f"Cannot read JSON file: {Path(path).name}") from error
    if not isinstance(value, dict):
        raise ValueError(f"{Path(path).name} must contain a JSON object")
    return value


def validate_config(config):
    errors = []
    unknown = set(config) - CONFIG_KEYS
    missing = CONFIG_KEYS - set(config)
    if unknown:
        errors.append("unsupported config keys: " + ", ".join(sorted(unknown)))
    if missing:
        errors.append("missing config keys: " + ", ".join(sorted(missing)))

    if config.get("app_type") != "Standard":
        errors.append("app_type must be Standard")
    mode = config.get("authorization_mode")
    if not isinstance(mode, str) or mode not in MODES:
        errors.append("authorization_mode must be foundry_oauth_passthrough or apim_credential_manager")
    if config.get("lookup_tool") != "Lookup":
        errors.append("only the read-only Lookup tool is allowed")
    if not isinstance(config.get("client_id"), str) or not config["client_id"].strip():
        errors.append("client_id must be provided")
    if not isinstance(config.get("lookup_arguments"), dict):
        errors.append("lookup_arguments must be a JSON object")

    for key in URL_KEYS:
        value = config.get(key)
        if not isinstance(value, str) or not value:
            errors.append(f"{key} must be provided from the ZoomInfo/APIM configuration")
            continue
        try:
            parsed = urlsplit(value)
            if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment:
                raise ValueError
        except ValueError:
            errors.append(f"{key} must be an absolute HTTPS URL without embedded credentials")

    scopes = config.get("scopes")
    if not isinstance(scopes, list) or not scopes or any(not isinstance(scope, str) or not scope.strip() for scope in scopes):
        errors.append("scopes must be a non-empty array of customer-approved scope strings")

    return errors


def evaluate(config, evidence):
    errors = validate_config(config)
    if errors:
        return {"decision": "STOP", "reasons": errors}

    malformed = set(evidence) - EVIDENCE_KEYS
    missing = EVIDENCE_KEYS - set(evidence)
    if malformed or missing:
        messages = []
        if malformed:
            messages.append("unsupported evidence keys: " + ", ".join(sorted(malformed)))
        if missing:
            messages.append("missing evidence keys: " + ", ".join(sorted(missing)))
        return {"decision": "STOP", "reasons": messages}

    boolean_keys = EVIDENCE_KEYS - {"lookup_http_status", "evidence_refs"}
    bad_booleans = [key for key in boolean_keys if not isinstance(evidence[key], bool)]
    if bad_booleans:
        return {"decision": "STOP", "reasons": ["evidence values must be booleans: " + ", ".join(sorted(bad_booleans))]}
    if evidence["lookup_http_status"] is not None and (
        not isinstance(evidence["lookup_http_status"], int)
        or isinstance(evidence["lookup_http_status"], bool)
        or not 100 <= evidence["lookup_http_status"] <= 599
    ):
        return {"decision": "STOP", "reasons": ["lookup_http_status must be an HTTP status integer or null"]}
    refs = evidence["evidence_refs"]
    if not isinstance(refs, list) or any(not isinstance(ref, str) or not ref.strip() for ref in refs):
        return {"decision": "STOP", "reasons": ["evidence_refs must be an array of non-empty redacted references"]}
    if any(re.search(r"(?i)(bearer\s+|client[_ -]?secret|access[_ -]?token|refresh[_ -]?token)", ref) for ref in refs):
        return {"decision": "STOP", "reasons": ["evidence_refs must not contain credentials or token material"]}

    status = evidence["lookup_http_status"]
    lookup_pass = (
        isinstance(status, int)
        and 200 <= status < 300
        and evidence["lookup_read_only_verified"]
        and not evidence["lookup_jsonrpc_error"]
        and not evidence["lookup_mcp_is_error"]
    )
    mode = config["authorization_mode"]
    if mode == "foundry_oauth_passthrough":
        complete = (
            evidence["authorization_succeeded"]
            and evidence["per_user_delegation_verified"]
            and lookup_pass
        )
        route = "Foundry per-user OAuth passthrough"
        blockers = []
        if not evidence["authorization_succeeded"]:
            blockers.append("Foundry OAuth authorization has not been proven at runtime")
        if not evidence["per_user_delegation_verified"]:
            blockers.append("per-user delegated identity has not been proven")
    else:
        complete = (
            evidence["authorization_succeeded"]
            and evidence["token_exchange_succeeded"]
            and evidence["pkce_s256_verified"]
            and lookup_pass
        )
        route = "APIM Credential Manager Authorization Code + PKCE"
        blockers = []
        if not evidence["authorization_succeeded"]:
            blockers.append("APIM Credential Manager authorization has not been proven at runtime")
        if not evidence["token_exchange_succeeded"]:
            blockers.append("APIM Credential Manager token exchange has not been proven")
        if not evidence["pkce_s256_verified"]:
            blockers.append("mandatory PKCE S256 has not been proven in the authorization request")

    unsafe_lookup = (
        isinstance(status, int)
        and 200 <= status < 300
        and not evidence["lookup_read_only_verified"]
    )
    if not lookup_pass:
        blockers.append("a successful, read-only ZoomInfo Lookup result has not been proven")
        if status is not None and not 200 <= status < 300:
            blockers.append(f"Lookup returned HTTP {status}")
        if evidence["lookup_jsonrpc_error"] or evidence["lookup_mcp_is_error"]:
            blockers.append("Lookup returned an MCP/JSON-RPC error")
        if not evidence["lookup_read_only_verified"]:
            blockers.append("Lookup read-only behavior has not been verified")
    if not refs:
        blockers.append("no redacted evidence references were recorded")
    failed_lookup = (
        (status is not None and not 200 <= status < 300)
        or evidence["lookup_jsonrpc_error"]
        or evidence["lookup_mcp_is_error"]
    )
    if complete and refs:
        decision = "GO"
        reasons = ["authorization and a successful read-only Lookup are evidenced for " + route]
    elif failed_lookup or unsafe_lookup or not evidence["lookup_read_only_verified"]:
        decision = "STOP"
        reasons = blockers
    elif evidence["token_exchange_succeeded"] or evidence["authorization_succeeded"]:
        decision = "PARTIAL"
        reasons = blockers
        if evidence["token_exchange_succeeded"] and not lookup_pass:
            reasons.append("token exchange alone is partial validation, not an end-to-end pass")
    elif status is not None or evidence["lookup_jsonrpc_error"] or evidence["lookup_mcp_is_error"]:
        decision = "STOP"
        reasons = blockers
    else:
        decision = "NOT_RUN"
        reasons = blockers
    return {"decision": decision, "route": route, "reasons": reasons}


def main():
    parser = argparse.ArgumentParser(description="Validate ZoomInfo test configuration or evaluate a redacted evidence record.")
    parser.add_argument("--config", required=True, help="path to a customer-filled, secret-free JSON config")
    parser.add_argument("--evidence", help="path to a redacted runtime evidence JSON record")
    parser.add_argument("--out", help="write an evaluated report JSON file")
    args = parser.parse_args()

    try:
        config = read_json(args.config)
        config_errors = validate_config(config)
        if config_errors:
            report = {"decision": "STOP", "reasons": config_errors}
        elif not args.evidence:
            report = {
                "decision": "READY_FOR_CUSTOMER_INPUT",
                "reasons": ["configuration is complete; no network request was made"],
            }
        else:
            report = evaluate(config, read_json(args.evidence))
        report["generated_at_utc"] = datetime.now(timezone.utc).isoformat()
        report["configuration"] = {
            key: config.get(key)
            for key in ("app_type", "authorization_mode", *URL_KEYS, "scopes", "lookup_tool")
        }
        output = json.dumps(report, indent=2) + "\n"
        if args.out:
            Path(args.out).write_text(output, encoding="utf-8")
        sys.stdout.write(output)
        if report["decision"] in {"STOP", "PARTIAL", "NOT_RUN"}:
            return 1
        return 0
    except ValueError as error:
        print(str(error), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
