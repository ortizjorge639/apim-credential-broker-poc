# Template: APIM Credential Manager broker for a Foundry agent (Approach A)

Deploys, from scratch: APIM (Consumption) + Credential Manager provider/connection (Entra v2, personal or work accounts), an MCP-style API (`/mcp/`) that calls Graph `/me` with the brokered token, caller auth via `validate-jwt` pinned to the Foundry project's managed identity, and a Foundry account/project/gpt-4o deployment/agent with a keyless (project managed identity) connection to APIM.

Agent + human-handoff version: see AGENT-RUNBOOK.md.

## Run
```
az login
./deploy.sh <suffix> [eastus2]     # ~5-10 min; prints a consent link at the end
# open the link in a browser signed in as the user to broker, accept, land on example.com
./teardown.sh <suffix>
```
Prereqs: `az`, `jq`, `curl`, Bicep (`az bicep install`); Contributor on the subscription; rights to create Entra apps (Application Developer or higher).

## Files
- `main.bicep`: all Azure resources. `mcp-policy.xml`: policy with `{{TENANT_ID}} {{APP_ID}} {{CALLER_OID}}` filled by Bicep.
- `deploy.sh`: imperative parts Bicep can't do (Entra app/secret/SP, agent via data plane, consent link).
- `teardown.sh`: RG delete, purge soft-deleted Foundry/APIM, delete Entra app.

## Verify
Run `./verify.sh <suffix>` for steps 1-3 automatically (Connected, 401 cases, agent answer). Manual list:
1. After consent: connection `aadv2/c2` status = `Connected`.
2. Foundry portal > Build > Agents > `whoami-agent` > Playground > "who is signed in" -> your display name.
3. Negative: call `https://<apim>.azure-api.net/mcp/` with no token -> 401; with a token from another identity (audience = app id) -> 401 (oid pin).

## Gotchas learned the hard way (details in R5.md)
- Use the generic `oauth2` provider with explicit v2 URLs. The built-in `aad` provider looped on consent for MSA/guest users.
- Set `requestedAccessTokenVersion=2` on the Entra app; create the SP (`az ad sp create`) or the MI can't get a token for the audience.
- Foundry MCP tool rejects inline `headers`; use `project_connection_id`. Connection names: alphanumerics, `-`, `.` only.
- Policy changes take ~30-60 s; `validate-jwt` errors need an `on-error` mapping to return 401, and the error body leaks detail (trim for production).
- Consent is one human step per connection. Refresh is automatic in APIM; see R5.md for the refresh/revocation result.
- Revoked/unconsented connection returns a clean 424 JSON (policy checks the variable); `validate-jwt` errors return 401 with no detail.
- Deploy creates a budget ALERT (`BUDGET_USD`, default 10; alerts only, does not stop spend). Model via `MODEL_NAME`/`MODEL_VERSION`.
- NOT proven / out of scope: per-user isolation (needs per-caller `authorization-id` from a user identity; app-only tokens map to one connection), per-user OAuth passthrough.
- GitHub provider (R1) is not templated here; add a second `authorizationProviders` resource with `identityProvider: 'github'` and your OAuth app credentials.
- Cost: Consumption APIM is ~free at PoC volume; Foundry/gpt-4o bills only per token. Add a budget on the RG.
