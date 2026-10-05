# Agent + human runbook (recreate the APIM broker PoC)

Roles: AGENT = coding agent with az/curl/jq. HUMAN = proxy who unblocks portals/consent and validates. Never ask the human for something the agent can do via CLI.

| # | Who | Step | Done when |
|---|-----|------|-----------|
| 0 | HUMAN | `az login` as an account with Contributor + Entra app-create rights; pick subscription; (optional) create $ budget on the RG | `az account show` OK |
| 1 | AGENT | Read README.md + main.bicep; run `./deploy.sh <suffix>` | Script prints consent link, APIM `/mcp/` w/o token returns 401 |
| 2 | HUMAN | Open consent link in a browser signed in as the user to broker (use Safari/Dia; passkeys may prompt), Accept, land on example.com | Page shows example.com |
| 3 | AGENT | Poll `authorizations/c2` until `properties.status == Connected` (ARM api 2022-08-01) | Connected |
| 4 | AGENT | Run agent via Foundry Responses API (`agent_reference: whoami-agent`); expect `mcp_call` output with the user's display name | Name returned |
| 5 | AGENT | `./verify.sh <suffix>`, plus negative tests: no token -> 401; other identity token -> 401; wrong redirect URI -> Entra invalid_request | All as expected |
| 6 | HUMAN | Portal validation + screenshots (RG, APIM > Credential manager > aadv2 > c2 Connected + access policy, Foundry > Connections `apimmcp-mi`, agent playground) | Screenshots saved |
| 7 | AGENT | Lifecycle tests: wait >1h, call again (token hash changes = refresh); delete c2 (agent must fail closed), regenerate login link | See R5.md |
| 8 | HUMAN | Re-consent link from step 7 | c2 Connected again |
| 9 | HUMAN approves, AGENT runs | `./teardown.sh <suffix>`; HUMAN deletes any GitHub OAuth app | RG gone, Entra app gone |

Rules for the agent: report blunt pass/fail with evidence; fix-forward on deploy errors (known: concurrent Foundry writes -> dependsOn; deploy.sh is idempotent); do not delete or spend beyond the budget without asking; use tenant-domain portal links (`#@<tenant>.onmicrosoft.com`), not GUID links.

Verified: a fresh deployment of this template passed steps 1-4 and the 424 fail-closed test (suffix t2). Step 4 can be run with `./verify.sh <suffix>`. Known unverified: per-user isolation.
