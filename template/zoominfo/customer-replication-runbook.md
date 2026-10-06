# Customer replication runbook: ZoomInfo MCP through APIM and Foundry

This runbook is a **planned, customer-environment procedure**, not a claim that the production integration has been deployed or validated. The local mock proves only synthetic protocol and architecture-shaped behavior. Run these steps only in a customer-approved non-production environment after the customer supplies the inputs below and approves the Azure resources, OAuth applications, costs, and test account.

For a shorter, portal-first walkthrough, start with [`portal-first-guide.md`](portal-first-guide.md). This runbook contains the detailed prerequisites, evidence rules, and safety gates.

## Scope and test lanes

Keep the two authentication-owner experiments separate. APIM is the governance point in both. Do not choose the eventual APIM-only versus APIM-plus-Toolbox product architecture as part of these tests.

| Lane | Credential/token owner | Goal | Full-pass evidence |
|---|---|---|---|
| A. Foundry OAuth passthrough (test first) | Foundry per-user delegated OAuth connection | Establish whether a Foundry user can call ZoomInfo Lookup through APIM with that user's delegated identity | Hosted Foundry calls the reachable HTTPS MCP endpoint through APIM; delegated user identity is verified; read-only Lookup succeeds with no HTTP, JSON-RPC, or MCP error |
| B. APIM Credential Manager (separate test) | APIM Credential Manager connection | Determine whether APIM can own the user OAuth connection and broker Lookup access | Actual APIM authorization request proves PKCE S256 and missing-challenge behavior; code exchange succeeds; read-only Lookup succeeds through APIM |

Do not interpret the local modes `foundry_oauth_passthrough` or `apim_credential_manager` as integrations with those Azure products. They are in-process simulations using synthetic tokens.

## Before starting: customer input and authorization

Name a customer technical owner and test user, and collect/approve the following in a customer-controlled secure channel:

| Input | Required detail | Owner / verification |
|---|---|---|
| ZoomInfo MCP service | Exact HTTPS base URL, Streamable HTTP transport/version, authentication expectations, documented tool catalog/schema | ZoomInfo service owner |
| Read-only operation | Confirm the exact `Lookup` tool and its schema are read-only; identify a safe, non-sensitive test query | ZoomInfo service owner |
| OAuth app | The current app is Standard and uses Authorization Code + PKCE. Confirm its client ID; issuer/authorize, token, and refresh URLs as applicable; exact approved delegated scopes; and app owner. Client Credentials is a supported but separate server-to-server mode, not a substitute for this per-user test. | Customer identity/app owner |
| Callback URLs | Obtain the exact registered callback URI separately for the Foundry client and APIM Credential Manager, from the actual product configuration. Do not reuse one callback by assumption. | Identity/app owner + platform owner |
| Test identity/access | Named non-production test user, consent path, ZoomInfo tenant access, and Lookup entitlement | Customer test owner |
| APIM/Foundry environment | Approved subscription/resource group/region, existing service names or approved new resources, network path, APIM gateway URL, Foundry project, and caller-authentication model | Azure platform owner |
| Guardrails | Budget/cost owner, approved test window, retention and redaction location for evidence, and cleanup/rollback approver | Customer platform/security owner |

**Do not begin a real test** if the MCP endpoint, app callbacks, scopes, Lookup read-only behavior, test account entitlement, or approval to use the environment is unknown. Do not put client secrets, access/refresh tokens, authorization headers, customer query/result data, or credential-bearing traces in this repository, shell history, work items, or evidence files. Enter secrets only in the customer's approved secret store or product connection UI.

## Important: what the current repository template does and does not deploy

`template/main.bicep`, `template/deploy.sh`, and `template/mcp-policy.xml` implement the existing Microsoft Graph `/me` APIM Credential Manager demonstration with a Foundry project-managed-identity caller. They are **not** a ready-to-deploy ZoomInfo passthrough stack:

- The policy calls Graph and retrieves an APIM Credential Manager token; it is not configured to forward a Foundry user's ZoomInfo bearer token.
- The sample validates one Foundry project managed identity, which proves caller gating, not delegated per-user identity. It binds one static Credential Manager authorization; dynamic per-user connection selection is not established by this sample.
- The Bicep OAuth provider is generic OAuth 2.0 without a documented IaC PKCE/S256 selector in the checked schema.
- The sample deploy script creates Azure resources and an Entra application/secret. Do not run it as a shortcut for this test.

Before any customer deployment, adapt and review a customer-specific APIM API/policy/backend and Foundry connection/agent setup. Starting points are [`policy-foundry-passthrough.xml`](policy-foundry-passthrough.xml) and [`policy-apim-credential-manager.xml`](policy-apim-credential-manager.xml). Both are drafts with placeholders, not deployable policy. The Foundry template assumes a signed ZoomInfo JWT with published OpenID metadata. The Credential Manager template assumes an Entra JWT caller and one fixed connection accessed by APIM's managed identity; it does not prove per-user connection selection. Confirm those assumptions against customer documentation and replace all placeholders. Keep the policy stateless for MCP streaming, preserve JSON-RPC/MCP responses, and ensure it forwards only the intended test user's delegated bearer for Lane A or obtains/injects the intended backend token for Lane B. Validate caller authorization and restrict the backend route to the customer-approved MCP endpoint. Configure the MCP server's Tools blade to expose only the approved read-only `Lookup` tool. Do not log Authorization headers, token-bearing query strings, or full Lookup payloads. Have the customer security/platform owner review the policy and OAuth trust boundary before saving or using it.

## Phase 1 — preflight the customer inputs

1. Gather the approved configuration inputs from the customer owners. The portal walkthrough does not require copying `config.example.json`, running Bicep, or running a deployment script. If the team chooses to use the optional offline validator, keep its config in an approved customer-controlled location outside the repository and never include `client_secret` or tokens.
2. Confirm `app_type` is `Standard`, the ZoomInfo app is configured for Authorization Code + PKCE, the `authorization_mode` is `foundry_oauth_passthrough`, endpoint URLs use HTTPS, and scopes exactly match the approved ZoomInfo registration. Do not switch to Client Credentials for the per-user delegated test.
3. Confirm the customer has documented `Lookup` as read-only and supplied only safe non-sensitive test arguments. The local config check restricts the tool name; it cannot validate ZoomInfo's actual semantics.
4. (Optional) If the team wants a local structural check of its recorded config, run the offline preflight from the repository root:

   ```sh
   python -B template/zoominfo/zoominfo_validation.py --config path/to/customer-config.json
   ```

   This validator is optional and is not part of the portal procedure. `READY_FOR_CUSTOMER_INPUT` means only that the local fields are complete and structurally valid. It makes no network call and is not a runtime pass.

5. Before any live operation, review the complete proposed APIM policy, target URL, caller auth, OAuth callback(s), requested scopes, logging/redaction, budget, and cleanup plan with the customer owner. Obtain the customer's explicit approval before creating/updating any cloud resources or app registrations.

## Phase 2 — prepare the non-production service path

6. In the customer-approved non-production environment, configure APIM as the controlled HTTPS entry point for the customer's MCP endpoint. This requires an APIM API/backend/policy appropriate to the chosen lane; the current Graph-specific sample policy must not be reused unchanged.
7. Keep caller authentication distinct from backend authentication:
   - For Lane A, authenticate/authorize the Foundry caller and forward only the per-user delegated ZoomInfo token according to the customer-reviewed design.
   - For Lane B, authenticate/authorize the Foundry caller and obtain the ZoomInfo backend token from the intended, explicitly authorized Credential Manager connection, then inject it only on the upstream request. If the test is intended to be per-user, separately establish a secure caller-to-user-connection binding; the repo's fixed connection ID does not prove this mapping.
8. Restrict upstream host/path, methods, and tools to the approved ZoomInfo MCP endpoint and read-only `Lookup` (plus required MCP initialization/list methods). Reject unrelated tools; avoid request/response body logging and secret-bearing diagnostics.
9. Verify from the Foundry service's network path that the registered HTTPS APIM MCP endpoint is reachable and its TLS certificate is valid. The loopback mock (`127.0.0.1`, plain HTTP) cannot be registered/reached by hosted Foundry. Do not tunnel or publish the mock.
10. Review the APIM and Foundry resource changes in deployment what-if/change preview and customer approval process. Apply only the explicitly approved changes; this runbook itself does not run deployment commands.

## Phase 3 — Lane A: real Foundry delegated passthrough

11. In Foundry, create/configure the per-user OAuth connection using the customer-approved Standard app's Authorization Code + PKCE settings, exact scopes, and the **Foundry-specific callback URI** registered by the identity owner. Store client credentials only in the approved secure connection configuration.
12. Configure a non-production test agent to connect to the APIM HTTPS MCP endpoint using the Foundry OAuth connection. Do not select a project/shared identity if the hypothesis under test is per-user delegation. Require human approval for tool calls if available during initial validation.
13. Sign in as the named test user and consent only to the approved scopes. Verify the connection reports authorized, but treat that as setup evidence only.
14. From the hosted Foundry agent, initialize the MCP session, list tools, and call only `Lookup` with the approved test arguments. Do not call a write/update/delete tool.
15. Capture redacted evidence that identifies the test run, timestamp, selected connection/mode, HTTP status, JSON-RPC error absence, MCP `isError=false`, and the synthetic/non-sensitive Lookup outcome. Separately verify through approved claims/diagnostics that the backend identity was the invoking user. Never save raw bearer tokens, secrets, full authorization URLs containing codes, or sensitive query/results.
16. If caller identity is shared/project-level, cannot be verified, the tool is not confirmed read-only, or any transport/protocol/provider error occurs, record `PARTIAL`/`STOP` and do not claim per-user pass. Diagnose only from redacted metadata; ask the product/service owner for missing details.

## Phase 4 — Lane B: APIM Credential Manager PKCE (separate test)

17. Proceed only after Lane A or its findings are recorded and the customer approves a separate APIM Credential Manager experiment. Create/use a separate provider and authorization connection; register the **APIM Credential Manager-specific callback URI** with the customer OAuth app owner.
18. Microsoft documents a portal option called **Generic OAuth 2.0 with PKCE**. Configure that option through the customer APIM portal in a non-production instance if approved. Do not add a guessed `identityProvider` string or PKCE property to Bicep/ARM: the repository's reviewed APIM `authorizationProviders` IaC schema does not document a selectable value/property that proves mandatory S256.
19. Using an approved diagnostic method that redacts secrets and authorization codes, verify the actual authorize request contains `code_challenge` and `code_challenge_method=S256`. If safe and supported, verify a request without the required challenge is rejected. Do not copy the `code_verifier`, raw authorization code, or tokens into evidence.
20. Complete a test-user consent and code exchange for the separate connection. A `Connected` status or successful token exchange alone is only partial. Verify which identity the backend request represents; do not call a shared credential per-user.
21. Through APIM, run MCP initialize/list and only the approved `Lookup`. Require successful HTTP status, no JSON-RPC error, MCP `isError=false`, documented read-only semantics, and redacted evidence. If S256 is unavailable/unverifiable, report the lane as blocked/partial; do not downgrade to ordinary authorization code and call it a PKCE pass.

## Phase 5 — evaluate and record results

22. Record observed outcomes in the customer's approved evidence system. The JSON example and validator are optional references; do not copy customer URLs, token material, or full response bodies into this repository.
23. (Optional) If the team uses the local evidence validator, set `authorization_mode` accurately in the customer config and run:

   ```sh
   python -B template/zoominfo/zoominfo_validation.py \
     --config path/to/customer-config.json \
     --evidence path/to/redacted-evidence.json \
     --out path/to/customer-result.json
   ```

24. Interpret `GO` only as this specific route reaching the evidence gate with the required flags. The tool cannot independently validate the truth of evidence values or prove ZoomInfo entitlements; evidence must be reviewed by the customer test owner.
25. Store the signed-off result and supporting redacted evidence in the customer's approved location. Do not check customer endpoint URLs, scopes, test identities, or evidence into this public/project repository without an explicit review and approval. The committed examples remain generic placeholders.

## Stop, rollback, and cleanup

- Stop on unclear consent, unexpected scopes, identity mismatch, unapproved data access, non-read-only tool behavior, token leakage, TLS/network/authentication failures, or a non-MCP response. Preserve only redacted diagnostic metadata and involve the customer's security/platform owner.
- Revoke test-user consent/authorization and remove the temporary Foundry connection/agent and APIM connection/policies/resources according to the customer's approved change/retention process. Confirm no active test credentials or public ingress remain. Do not delete a shared RG, app, APIM service, or customer resource unless its exact ownership/scope is verified and the customer explicitly approves deletion.
- Record cleanup owner, date, resource identifiers in the customer system of record (not the repository), and whether any approved test credential rotation/revocation is needed.

## Confidence statement and known boundary

The repository currently contains a local OAuth/MCP mock and a Foundry-like/APIM-like end-to-end simulation. It exercises synthetic authorization-code PKCE S256, token separation/injection, a read-only tool allowlist, and mock Lookup. It does **not** run Azure Foundry, Azure APIM, Credential Manager, or ZoomInfo. A local `PASS` is not a customer-environment pass. The customer test above is required to validate hosted Foundry delegated behavior, actual APIM gateway/policy behavior, ZoomInfo scopes/entitlements, and Credential Manager's real PKCE behavior.

## Useful repository files

- [`README.md`](README.md) — local mocks and validation harness
- [`test-readiness.html`](test-readiness.html) — interactive prerequisites and test-lane visual
- [`mock_service.py`](mock_service.py), [`local_e2e.py`](local_e2e.py) — loopback protocol/architecture simulations
- [`zoominfo_validation.py`](zoominfo_validation.py) — offline config/evidence gate
- [`../main.bicep`](../main.bicep), [`../mcp-policy.xml`](../mcp-policy.xml), [`../deploy.sh`](../deploy.sh) — existing Graph PoC only; not a ZoomInfo deployment
- [`../AGENT-RUNBOOK.md`](../AGENT-RUNBOOK.md) — original general PoC human/agent runbook
