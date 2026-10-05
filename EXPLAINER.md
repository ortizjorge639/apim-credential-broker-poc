# Explainer: how the APIM Credential Manager broker works, and how we built and proved it

For humans learning the topic. Read top to bottom; each phase says what we tried, what happened, and what it teaches. Per-route raw logs are in `R0.md`-`R6.md`.

## 1. The problem
An AI agent (Foundry) needs to call an API **as a particular user** (here: Microsoft Graph `/me`). The naive options are bad:
- Put the user's token or a secret in the agent/tool config: it leaks into prompts, logs and configs, and it expires.
- Make every agent implement OAuth: consent screens, refresh, revocation, all re-built per tool.

**Approach A (this PoC): a broker.** APIM Credential Manager does the OAuth dance once with the human, stores and refreshes the token, and a gateway policy attaches it to the outbound call. The agent only ever talks to APIM and never sees the user's token.

## 2. Vocabulary
| Term | Meaning |
|---|---|
| **Authorization provider** | The OAuth client config in APIM: identity provider, client ID/secret, scopes, authorize/token URLs. |
| **Authorization (connection)** | One consented grant under a provider (`c2`). Holds the access + refresh token. Has a status: Connected / Error. |
| **Access policy** | Who may *use* the connection: `{objectId, tenantId}`. We grant the APIM system identity. |
| **Consent** | A one-time human browser step (login link) that makes the connection Connected. |
| **APIM system identity** | The managed identity APIM uses to read the token from the vault in policy. |
| **Project managed identity (MI)** | The Foundry project's identity. The agent uses it to authenticate to APIM with no secret. |
| **validate-jwt** | APIM policy that checks the caller's token (audience, issuer, claims). |
| **MCP API** | A minimal Model Context Protocol endpoint (JSON-RPC: `initialize`, `tools/list`, `tools/call`) implemented as an APIM API at `/mcp/`. |

## 3. The final architecture
Two separate identities, easy to confuse:
1. **Caller -> APIM (who may call the gateway):** the agent presents a managed-identity token for the app registration's audience; `validate-jwt` checks audience, issuer and pins the caller's `oid` to the project MI.
2. **APIM -> Graph (whose data):** `get-authorization-context` reads the stored Entra token for connection `c2`, `send-request` calls Graph with `Bearer <token>`.

Runtime call:
1. Agent decides to call tool `whoami`; Foundry sends `POST /mcp/` with an MI token (no stored secret).
2. APIM `validate-jwt`: wrong audience, issuer or oid -> 401.
3. APIM `get-authorization-context` (as APIM identity) returns the cached token; if expired, Credential Manager refreshes it with the refresh token first.
4. APIM calls `GET https://graph.microsoft.com/v1.0/me` with the token.
5. The profile returns through APIM to the agent: "Signed-in user: <name>".

One-time consent: `getLoginLinks` returns a URL; the human opens it, signs in to Entra, accepts, and is redirected to the registered redirect URI; APIM stores the tokens.

Important limit: connection `c2` is **one shared identity**. Anyone who passes `validate-jwt` gets the same user's data. This is not per-user isolation.

## 4. The build, phase by phase
### R0 Setup
Resource group, a $5 budget alert (alerts only, it does not stop spend), a Consumption-tier APIM. *Lesson:* Consumption is effectively free at PoC volume; budget alerts notify, they don't enforce.

### R1 Core broker with GitHub (PASS)
Provider `github` + connection, an API that calls `get-authorization-context` then sets the `Authorization` header, backend `api.github.com`.
*Surprise:* with the connection **Connected** but no access policy for the APIM identity, the call returned **500**. Connected is not enough; the access policy is what authorizes the gateway to read the token. *Lesson:* three things must exist: provider, consented connection, access policy.

### R2 Gating callers with `identity-type="jwt"` (limited)
Idea: let the caller's own token choose who may use the connection. Every user token failed with "Permission denied" even with matching access policies; the portal blade exposed no access-policy section. It only worked for **app-only service-principal tokens** with audience `https://azure-api.net/authorization-manager`. *Lesson:* this is a gate on *which app* may use a connection, not user-level isolation; grants apply fast, revocations lag.

### R3 Dynamic connection choice (mechanism PASS)
`authorization-id` computed per request: consented id -> 200, unconsented or nonexistent -> 403. *Lesson:* per-user routing is possible by deriving the id from a **validated** claim (e.g. `oid`). Taking it from a query string would let anyone pick any connection. Two real users were not tested (one account available).

### R4 Failure of an unusable connection (finding differs from the cheat sheet)
For a never-consented connection, `get-authorization-context` did **not** fail and no 424 appeared; the auth variable was simply unset, and a 500 only occurred later when something dereferenced it (or the backend was called unauthenticated). *Lesson:* always check the variable exists and return your own 401/403/424, otherwise unauthenticated calls leak to the backend.

### R6 Wrong audience (PASS)
Same SP, two tokens: right audience -> 200; Graph audience -> "Bad authorization token" (surfaced as 500 until mapped). *Lesson:* map policy errors to 401 in an `on-error` block.

### R5 Real target: Entra / Graph, real Foundry agent (the long one)
1. **Consent loop.** The built-in `aad` provider (v1, tenant-prefixed endpoint) looped on consent for a personal/guest account. *Fix:* the generic `oauth2` provider with explicit v2 URLs (`login.microsoftonline.com/common/oauth2/v2.0/{authorize,token}`), scopes `User.Read offline_access`. On the Entra app set `requestedAccessTokenVersion=2` first, then widen `signInAudience` to personal + work accounts.
2. **End to end.** Graph `/me` returned the user; `/me/messages` returned 403 (scope not granted, as intended); a wrong redirect URI was rejected by Entra (`invalid_request`).
3. **Foundry agent.** Account + project + gpt-4o + a `whoami-agent` with an MCP tool pointing at APIM. Foundry rejects inline `headers`, so auth goes through a **project connection**. First version used a static bearer (expires in ~1 hour); final version uses a `ProjectManagedIdentity` connection (keyless) and APIM pins the caller `oid`.
4. **Caching.** Repeat calls returned the same token (same hash), so the vault serves a cached token instead of re-minting.
5. **Refresh after expiry.** About an hour later the same endpoint returned a *new* token hash with HTTP 200: APIM refreshed with the refresh token, no human involved. PASS.
6. **Revocation and recovery.** Deleting connection `c2` made the agent fail closed (APIM 500, no user data). Regenerating the login link and re-consenting restored it; the agent answered again. PASS. *Gap:* the original policy returned a leaky 500; the hardened template policy returns a clean 424 (verified on a fresh deployment).

## 5. Principles this teaches
- The agent holds **no user credential**; consent, storage, refresh and revocation live in one place (APIM).
- **Authenticate the caller separately** from brokering the user token, or the broker is an open door ("anyone with the URL acts as the user").
- **Never trust client input to choose a connection**; derive it from a validated claim.
- **Fail closed and say so**: map missing or unconsented connections and token errors to explicit status codes; trim error bodies.
- Prefer **managed identity** over static bearers: static ones expire (~1 h) and must be stored somewhere.

## 6. What is not proven
- Per-user isolation (needs two users and a per-caller connection), and Approach B (per-user OAuth passthrough).
- Behavior at scale, other regions/tiers (Consumption tier only, one region), and long-term refresh-token expiry (we observed one refresh cycle).

Proven since the original PoC: a from-scratch deploy of `template/` (suffix `t2`), consent, `verify.sh` 4/4 PASS (Connected, 401 no token, 401 garbage token, agent returns the signed-in user), and the hardened policy returns a clean **424** JSON error when the connection is deleted (instead of the earlier leaky 500).

## 7. Reproducing it
Use `template/AGENT-RUNBOOK.md`: the coding agent runs the CLI, a human provides login, the consent click and portal validation.
