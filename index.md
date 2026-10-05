# APIM Credential Manager PoC (Approach A) - index
Sub: Visual Studio Enterprise <subscription-id> | RG: rg-apim-cm-poc (eastus2) | Cap: $5 hard limit
APIM: <apim> (Consumption). Budget alert "poc-cap" on RG at 50%/90%.
Knowledge basis: ../mcp-oauth-foundry-apim.md

| Route | Question proven | File | Status |
|---|---|---|---|
| R0 | Setup: RG, budget, APIM | R0.md | done |
| R1 | Core broker flow: get-authorization-context -> set-header -> backend | R1.md | PASS |
| R2 | identity-type="jwt": access-policy gate; shared token for all callers | R2.md | PASS for SP (user JWT unsupported; revoke lag) - see R2.md |
| R3 | Dynamic authorization-id from oid claim (notebook: inferred) | R3.md | pending |
| R4 | Failure modes: status code on revoked/expired connection (424 vs 500) | R4.md | PASS (finding) |
| R5 | Token caching Developer vs Consumption (needs spend approval, skipped by default) | R5.md | PARTIAL, see row below |

Teardown: az group delete -n rg-apim-cm-poc --yes; delete Entra apps + GitHub OAuth app;
az apim deletedservice purge if name reuse needed.

## Lockdown
Deleted op getuser (/gh/user) so the unauthenticated managed-identity route no longer exposes the GitHub profile. Remaining resources kept; teardown pending user approval.
| R3 | dynamic authorization-id | R3.md | PASS (mechanism; 2-user isolation untested) |
| R6 | wrong audience rejected | R6.md | PASS |
| R5 | token cache/refresh | R5.md | PARTIAL: consent fixed via generic oauth2 v2 provider; cache reuse proven; refresh unproven; Graph e2e 200 + insufficient-scope 403 pass; real Foundry agent + JWT-protected APIM MCP PASS; redirect mismatch PASS |
| – | Evidence | screenshots: see (screenshots not published) |
- R5 refresh after expiry: PASS (00:57, new hash <hash2>, HTTP 200).
- R5 revocation fail-closed (500) + recovery after re-consent: PASS (00:59).
