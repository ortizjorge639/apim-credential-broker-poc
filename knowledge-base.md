# OAuth-protected MCP: Foundry, APIM, and identity

Captured 2026-10-03 from the author's NotebookLM notebook "OAuth-Protected MCP:
Foundry, APIM, and Identity" (opened in Dia, read via computer-use), then
cross-checked against the MCP spec and Microsoft Learn.

## Notebook inventory (verified by reading it)
- 22 sources. Microsoft Learn: APIM Credential Manager, get-authorization-context
  policy, Manage Connections for End Users, Connect and Govern Existing MCP server,
  Manage MCP Servers Programmatically, Secure access to MCP servers, inbound
  private endpoint, Create Connection to GitHub API. Foundry: Connect agents to MCP
  endpoints, Set up MCP server authentication, Toolbox (what is / quickstart),
  Agent Service networking. MCP spec: Authorization, Security Best Practices.
  GitHub: GitHub Apps vs OAuth apps, auth on behalf of a user, github-mcp-server.
  Other: Authorization Code Flow (PKCE), Refresh Tokens Flow, Connect to ZoomInfo MCP.
- Studio outputs: video "How MCP Servers Authenticate" and audio deep dive "Securing
  MCP Tokens for AI Agents" (cannot review media), infographic "OAuth Architecture
  Cheat Sheet" (read), slide deck (scheduled for after 12am, not generated yet).
- Chat tab has a long history (usage-limit banner, resets 12:41 AM); only the tail
  (checklist) was read. Earlier turns may hold the requirements.
- Notes: "MCP Identity Boundaries" (read, last checklist item truncated) and
  "Cloud Security Integration" (read). The first note labels itself AI-generated
  secondary material, not official docs.

## Core model (from the notes)
Two routes, defined by who owns the token lifecycle:
- A: APIM credential broker. Client auths to APIM (subscription key or Entra JWT);
  APIM runs get-authorization-context, gets/refreshes the backend token from
  Credential Manager, injects Authorization, proxies to the MCP server.
- B: Foundry custom OAuth proxy. Foundry Agent Service owns project- or user-level
  OAuth connections; APIM is a passthrough enforcing network isolation/traffic rules.

Six identity roles: Caller, Inbound Gateway, OAuth Client Registration, Token Issuer,
Token Lifecycle Owner, Backend Identity (the downstream provider account, not the
token string).

Key distinctions / cautions:
- identity-type="jwt" validates the caller's Entra JWT against a statically named
  connection ID = user-gated authorization. It does NOT prove per-user backend
  isolation; that needs per-user delegated connections.
- HTTP 200 is not success: need JSON-RPC error null and result.isError false.
- Reaching a Toolbox endpoint over HTTP/SSE proves transport only, not catalog
  attachment, RBAC, or multi-agent reuse.
- Refresh behavior (offline_access, PKCE) varies by IdP; never assume it.
- Log metadata (issuer, scopes, token presence), never raw tokens. Missing APIM logs
  are inconclusive without checking logging config and ingestion delay.

Validation checklist (5 tests, all read; #5 from the Chat tab): success path; expired
token + valid refresh token refreshes silently; wrong aud rejected with 401; insufficient
scope returns 403 with WWW-Authenticate insufficient_scope (step-up flow); redirect URI
mismatch during code exchange makes the IdP abort with redirect_uri_mismatch.

## Cheat sheet infographic (read 2026-10-03, verified visually)
- A: Caller Auth -> Access Policy -> Stored OAuth Connection (lifecycle owner: APIM)
  -> Provider Token Injection -> Backend Account. Token issuer: provider.
- Foundry Toolbox = packaging layer: bundles tools into one MCP endpoint, delegates
  identity/token handling to the underlying runtime.
- B: User Consent -> Provider User Token -> APIM Reverse Proxy (lifecycle owner: User,
  as drawn) -> MCP backend as user. Caveat: the notes text says Foundry owns the
  lifecycle in B; the infographic says "User". Inconsistent wording, unresolved.
- JWT gate does not force per-user tokens (static connections, not dynamic isolation).
- Status codes: 401 = missing caller token or expired backend client secret
  (re-auth / update secret); 403 = insufficient scope or failed access policy
  (step-up with scope challenge); 424 Failed Dependency = downstream provider token
  fetch/refresh failed (re-authorize user connection). 424 is not in the MCP spec
  and is notebook/APIM-specific (unverified).
- Telemetry: log issuer and scopes, mask token strings; test refresh tokens, PKCE,
  offline_access.

## Cross-reference
Verified against the MCP draft spec (modelcontextprotocol.io, fetched 2026-10-03):
- Auth is optional; HTTP transports SHOULD follow it, STDIO should use env creds.
- MCP server = OAuth 2.1 resource server; client = OAuth 2.1 client.
- Servers MUST implement RFC 9728 Protected Resource Metadata; clients MUST use it
  for authorization-server discovery. AS metadata via RFC 8414 or OIDC Discovery.
- Client registration priority: Client ID Metadata Documents (SHOULD), pre-registration,
  Dynamic Client Registration (deprecated, kept for compatibility).
- Servers SHOULD put scope in the WWW-Authenticate header; step-up re-auth on 403
  insufficient_scope. Resource Indicators (RFC 8707) bind token audience.
  This matches the notes' wrong-audience (401) and insufficient-scope (403) tests.
Verified against Microsoft Learn (Credential Manager overview): it manages, caches and
refreshes OAuth 2.0 backend tokens with no custom code; connections were formerly
"authorizations". Matches the notes' Approach A.

Not independently verified (taken from the notebook): Foundry-specific behavior
(Toolbox, project vs user connections, bearer forwarding) and the 424 mapping.

## Open follow-ups
- Read earlier Chat history for requirements; read the slide deck once generated.
- Confirm the Foundry claims against current Foundry docs before relying on them.
- A decision is still open: choose Approach A or B for the actual deployment.

## Tooling quirk (verified)
Dia page content is exposed through accessibility only after the page is focused and
interacted with. Background clicks on page content often do nothing; they worked
once the user was not actively typing in Dia. Pixel clicks need a fresh
get_window_state capture_mode=image first. Index clicks on web buttons often no-op.

## Extended chat synthesis (pasted by user 2026-10-03; NotebookLM AI output, secondary)
Resolved: Approach B owner = Foundry Agent Service (project-level OAuth connection,
tokens stored per user/connection); APIM = transparent passthrough (streamable/sse).
Infographic "User" wording is loose. Axes listed as 5 here (Caller, OAuth Client
Registration, Token Issuer, Lifecycle Owner, Backend Identity); earlier note says 6
(adds Inbound Gateway).
- identity-type="jwt": gate on ONE static authorization-id; shared token for all
  permitted callers. Per-user needs user-delegated connections or dynamically computed
  authorization-id (must be integration tested).
- Token passthrough (client token forwarded downstream) is forbidden by MCP spec
  (aud mismatch, confused deputy). Gateway token injection/exchange is OK.
- Foundry blocks Microsoft-audience tokens to custom MCP endpoints
  ("Cannot pass Microsoft token to untrusted MCP endpoint"); use custom OAuth app
  with developer-owned audience.
- PKCE S256 required; clients must abort if code_challenge_methods_supported missing.
  Refresh rotation = single use; offline_access needed; failure -> invalid_grant,
  re-authorize. APIM caches tokens until ~3 min before expiry (classic/v2), none in
  Consumption. Credential Manager encryption: AES-128, key rotated monthly (Key Vault).
- Networking: Foundry BYO VNet needs delegated subnet (/24 recommended), RFC1918 only
  (no CGNAT 100.64/10), tool traffic via single-tenant data proxy; private MCP on
  Container Apps internal ingress. APIM private endpoint only on Gateway sub-resource.
- Streaming: never read context.Response.Body in APIM policies; set frontend response
  payload bytes logged = 0 (buffering breaks SSE). Foundry non-streaming tool call
  timeout 100 s (use Background Mode / MCP Tasks beyond).
- Foundry connection types (azd ai connection create): none, custom-keys, oauth2,
  user-entra-token, project-managed-identity, agentic-identity. APIM supports type 'mcp'
  APIs with tools sub-resources.
- Guardrails: don't assume GitHub EMU readiness for remote MCP (SAML sessions; GHES no
  remote server), generic IdP PKCE, auto user isolation, or multi-agent token reuse.
- Test 5 detail: redirect mismatch is rejected at the authorization request, before
  consent, with invalid_request/redirect_uri_mismatch; no code issued.
- Offered next artifacts (not made): APIM policy XML, Bicep for private APIM MCP,
  Foundry SDK (AIProjectClient) approval-loop samples.
Verification: numbers (100 s, /24, AES-128, 3 min) are from notebook sources, not
independently re-checked by me.

## Approach A deep-dive Q&A (asked notebook 2026-10-03; answers tagged Documented/Inferred by NotebookLM, cites its 22 sources)
Documented (per notebook, with source citations):
- Flow: caller auths to APIM (sub key or Entra JWT) -> get-authorization-context
  (provider-id, authorization-id, context-variable-name, identity-type managed|jwt,
  identity, ignore-error) -> Credential Manager returns cached token or refreshes via
  stored refresh token (rotates it) -> Authorization object (AccessToken, Claims) in
  context.Variables -> set-header "Bearer "+AccessToken overrides outbound header ->
  forward (some backends need User-Agent, e.g. GitHub).
- jwt identity: JWT must have audience https://azure-api.net/authorization-manager,
  oid, tid. APIM matches oid/tid to the connection's Access Policy; listed -> token released.
- Grant types: Authorization Code = user-delegated, each user consents interactively at
  connection setup; Client Credentials = unattended, admin consents (client id/secret).
- Static unattended connection = one shared backend credential for all permitted callers.
  Per-user = user-delegated connection per user (each consents).
- authorization-id supports policy expressions (docs example uses a query param).
- Failed token acquisition with ignore-error=false -> internal HTTP 500 / context.LastError.
- 401: bad/missing inbound creds at gateway, or downstream rejects injected token.
  403: valid token lacks scope (MCP spec: WWW-Authenticate insufficient_scope), also
  public access disabled on APIM and call from public IP.
- Caching: classic/v2 cache until 3 min before expiry; Consumption does NOT cache.
Inferred only: mapping JWT oid to per-user connection id (authorization-id="conn-"+oid)
is an architectural pattern, not documented; MCP passthrough proxies JSON-RPC unaltered;
424 is NOT a documented APIM status (NotebookLM calls it an inferred WebDAV/proxy code;
documented failure is 500/LastError). A 424 or missing logs does not prove VNet outage.
Correction: earlier "424 = token fetch failed" from infographic is unconfirmed.

## Verified in PoC (Consumption, 2026-10-03)
- managed identity: needs system identity enabled + access policy {objectId,tenantId}; else 500.
- Unconsented connection: get-authorization-context does not throw; variable unset -> guard with ContainsKey, return 401/403 yourself. 424 not observed.
- jwt: only SERVICE PRINCIPAL app-only tokens (aud https://azure-api.net/authorization-manager, /.default) match an SP access policy; delegated user tokens denied even with oid policy. Gate, not per-user isolation.
- Access-policy deletion not enforced for 10+ min; deleting the connection itself revokes within ~20s. Don't rely on policy delete as instant revoke.
- Per-user backend tokens (R3) and Foundry integration: not tested.

## APIM R5 findings (PoC)
- Entra via built-in `aad` provider looped for an MSA/guest user; use a generic `oauth2` provider with explicit v2 authorize/token/refresh URLs (common endpoint) and an app with signInAudience AzureADandPersonalMicrosoftAccount (set requestedAccessTokenVersion=2 first).
- `loginUri` is a base URL; APIM appends the tenant + /oauth2/authorize path.
- Consumption tier returns the same cached token across calls (verified by hash). Refresh after expiry still unverified.
