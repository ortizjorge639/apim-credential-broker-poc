# ZoomInfo MCP validation (no deployment)

This folder contains a customer-run validation harness and separate loopback-only mocks, not a deployment or a live ZoomInfo probe. The validator is offline; the mocks accept only synthetic local OAuth test values. They never handle customer OAuth credentials.

For a visual readiness map, open [`test-readiness.html`](test-readiness.html).
For the portal-by-portal customer walkthrough, start with [`portal-first-guide.md`](portal-first-guide.md). For the detailed replication procedure and approval gates, use [`customer-replication-runbook.md`](customer-replication-runbook.md).
The two APIM policy files, [`policy-foundry-passthrough.xml`](policy-foundry-passthrough.xml) and [`policy-apim-credential-manager.xml`](policy-apim-credential-manager.xml), are parameterized drafts only. They contain no customer URLs or credentials. Confirm their assumptions, replace the placeholders, and obtain customer review before saving either policy in APIM.

## Local mock (protocol tests only)

Start the dependency-free mock issuer and MCP server from the repository root:

```sh
python template/zoominfo/mock_service.py
```

It binds only to `127.0.0.1` (default port `8765`; use `--port 0` to choose an available port). `/oauth/authorize` requires Authorization Code + PKCE S256, `/oauth/token` validates the verifier and consumes each synthetic code once, and `/mcp` serves Streamable HTTP JSON-RPC with only a read-only `Lookup` over fictional in-memory data. The callback displays only a synthetic local test code. Logs are suppressed and OAuth state is process-local/in-memory.

This single service validates local protocol behavior only. It does **not** prove ZoomInfo endpoints, scopes, app registration, entitlements, real identity claims, APIM Credential Manager compatibility, or a real provider's PKCE behavior. It does **not** prove Foundry's hosted per-user delegated flow: Foundry must be able to reach a registered HTTPS endpoint, and this service is deliberately loopback-only with plain HTTP. Do not tunnel, expose publicly, use real customer credentials/data, or register this mock as a customer app.

## Local end-to-end architecture simulation

Run the two in-process routes from the repository root (each command starts mock services on ephemeral loopback ports and shuts them down when finished):

```sh
python -B template/zoominfo/local_e2e.py --mode foundry_oauth_passthrough
python -B template/zoominfo/local_e2e.py --mode apim_credential_manager
```

Both modes use the local issuer for an Authorization Code + PKCE S256 exchange, then a Foundry-like caller performs MCP initialize, initialized notification, tools/list, and read-only Lookup through an APIM-like loopback gateway. The Foundry-passthrough simulation has separate synthetic caller and delegated-user bearer values. The APIM Credential Manager simulation holds the backend token in gateway process memory; the caller cannot choose or override it. In both cases the gateway checks the synthetic caller token, permits only initialize/ping/list/Lookup, and replaces the inbound caller authorization with the backend bearer on the loopback upstream call.

These are **architecture/protocol simulations**, not the Azure products. The caller token is a static synthetic fixture, not a signed JWT; the separate Foundry user-token header is a mock-only transport convention, not a documented Foundry connection contract; the APIM-like broker's in-memory token slot is not Credential Manager. The local server is plain HTTP on `127.0.0.1` only. A printed `PASS` means only that this local mock flow completed with synthetic data. It does **not** establish hosted Foundry reachability, Foundry per-user token behavior, real APIM policy semantics, Credential Manager PKCE selection/enforcement, or ZoomInfo behavior. No customer input is needed for this confidence-building simulation; real provider/product validation remains a separate later step.

Run its focused protocol tests:

```sh
python -B -m unittest discover -s template/zoominfo -p "test_mock_service.py" -v
python -B -m unittest discover -s template/zoominfo -p "test_local_e2e.py" -v
# or run all focused validator and mock tests
python -B -m unittest discover -s template/zoominfo -v
```

## Sequence and stop/go gate

1. **Baseline: Foundry OAuth passthrough.** Configure Foundry's per-user delegated OAuth connection for the ZoomInfo MCP endpoint, with APIM remaining the governance/proxy layer. In a real Foundry agent session for each test user, call only the ZoomInfo `Lookup` tool using customer-approved, non-sensitive test input. This route is a pass only when authorization succeeds, the call returns a successful MCP result, the tool's read-only behavior is verified, and the delegated identity is proven to be the invoking user. A shared/project token is not per-user proof.
2. **Candidate: APIM Credential Manager.** Test Authorization Code + PKCE first; do not silently fall back to non-PKCE authorization code. Use a separate test record/connection. Verify the actual authorization request contains both `code_challenge` and `code_challenge_method=S256`, that the provider rejects authorization without the required challenge if that can be tested safely, and that the complete Lookup succeeds. A token exchange by itself is only partial validation.
3. **GO** only when the selected route has runtime evidence for its authorization requirements and a successful read-only Lookup. **PARTIAL** means token/authorization progress without that full Lookup proof or without required PKCE/per-user proof. **STOP** on missing customer inputs, failed authorization, HTTP/JSON-RPC/MCP error, or unverified read-only behavior. Do not infer a pass from connection status, token exchange, or HTTP 200 alone.

Do not decide whether the final product uses APIM alone or APIM plus Toolbox here; APIM remains the governance layer in either test.

The current ZoomInfo app is a **Standard** app configured for **Authorization Code with PKCE**, which is the flow used by these two test lanes. ZoomInfo also supports **Client Credentials** for automated server-to-server access. That is a different authentication mode and does not establish per-user delegated behavior; it is not used as a fallback for a failed PKCE test.

## Configuration

Copy `config.example.json` to a private working location and fill only customer-provided values:

- `app_type`: the approved assumption is `Standard`.
- `authorization_mode`: `foundry_oauth_passthrough` for the per-user baseline, or `apim_credential_manager` for the subsequent broker test.
- `client_id`, authorization/token/refresh URLs, redirect URL, exact scopes, APIM MCP endpoint, and `lookup_arguments`: supply from the ZoomInfo app registration, customer APIM instance, and documented ZoomInfo MCP tool schema. Do not guess URLs/scopes or use production records as test input.
- Only `Lookup` is allowed by the config gate. Confirm that the server documents it as read-only before running a test. If the published tool metadata cannot establish this, stop rather than substitute another tool.
- Never add `client_secret`, access/refresh tokens, authorization headers, or credentials to config or evidence files. Use the customer-approved secret store/Foundry connection UI for credentials.

Check configuration without network access:

```sh
python3 template/zoominfo/zoominfo_validation.py --config path/to/config.json
```

This prints `READY_FOR_CUSTOMER_INPUT` only after required fields validate; it does not contact Foundry, APIM, or ZoomInfo. The included evidence example is a blank record. After a customer-run test, fill it with observed booleans/status and redacted evidence references, then evaluate and save a report:

```sh
python3 template/zoominfo/zoominfo_validation.py --config path/to/config.json --evidence path/to/evidence.json --out path/to/result.json
```

This validator is optional. The portal-first customer procedure does not require it, Bicep, or deployment scripts. The report contains configuration metadata and pass/partial/stop reasons, not request/response bodies or credentials. Store config/evidence/result outside the repository if they contain customer-specific URLs, scopes, or test details. `READY_FOR_CUSTOMER_INPUT` is a successful structural preflight only; `GO` is the only zero-exit completed evidence check. Neither status proves that the portal resources, network paths, or ZoomInfo behavior are ready.

In the evidence record, set `lookup_read_only_verified` only after confirming the `Lookup` tool's documented behavior; `lookup_http_status` is the actual tool-call HTTP status; the two error flags capture JSON-RPC and MCP `isError`; `per_user_delegation_verified` requires evidence that the delegated identity is the invoking user. Set `pkce_s256_verified` only after observing `code_challenge_method=S256` on the actual APIM authorization request. Keep `evidence_refs` redacted and point them to customer-controlled evidence, not copied tokens or response bodies.

## PKCE capability blocker

Microsoft's current Credential Manager guidance documents a portal choice for **Generic OAuth 2.0 with PKCE**. However, the repository's APIM provider uses `Microsoft.ApiManagement/service/authorizationProviders@2022-08-01`, and the reviewed ARM/Bicep provider contract (including the published `2024-05-01` schema) exposes `identityProvider` as an unconstrained string and the authorization grant payload as open string properties. It does not document a provider enum/value or property that selects PKCE or mandates S256. Do not add a guessed `identityProvider` value or a guessed PKCE property.

Therefore, PKCE is **documented as a portal capability but not yet expressible/proven through this repository's checked IaC/API surface**. A customer can establish whether the portal-created provider emits mandatory S256; capture redacted evidence of the authorization request and the no-PKCE rejection. Until then, the APIM Credential Manager route is blocked/partial, not a pass. If IaC/API automation is required, first obtain the exact supported resource contract/value from Microsoft and verify it against the actual APIM instance.

References:

- [Configure common credential providers in Credential Manager](https://learn.microsoft.com/azure/api-management/credentials-configure-common-providers#generic-oauth-providers)
- [APIM authorizationProviders ARM schema, 2024-05-01](https://learn.microsoft.com/azure/templates/microsoft.apimanagement/2024-05-01/service/authorizationproviders)
