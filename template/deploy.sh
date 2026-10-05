#!/usr/bin/env bash
# Reproduces the APIM Credential Manager broker PoC from scratch.
# Usage: ./deploy.sh <suffix> [location]   (suffix: short lowercase, makes names globally unique)
# Optional env: BUDGET_USD (default 10, 0 = skip), MODEL_NAME/MODEL_VERSION (default gpt-4o / 2024-11-20).
# Needs: az (logged in, Contributor + permission to create Entra apps), jq, python3, curl.
set -euo pipefail
export PYTHONWARNINGS=ignore
SUF=${1:?suffix required}; LOC=${2:-eastus2}
RG=rg-apim-cm-$SUF; APIM=apim-cm-$SUF; FDRY=foundry-cm-$SUF; PROJ=proj1
SUB=$(az account show --query id -o tsv); TENANT=$(az account show --query tenantId -o tsv)
EMAIL=$(az account show --query user.name -o tsv)
HERE=$(cd "$(dirname "$0")" && pwd)

echo "== 1. Resource group"
az group create -n "$RG" -l "$LOC" -o none
# Budget ALERT only (does not stop spend). Override with BUDGET_USD=0 to skip.
BUDGET_USD=${BUDGET_USD:-10}
if [ "$BUDGET_USD" != "0" ]; then
  az rest --method put -o none --url "https://management.azure.com/subscriptions/$SUB/resourceGroups/$RG/providers/Microsoft.Consumption/budgets/cap?api-version=2023-05-01" \
    --body "{\"properties\":{\"category\":\"Cost\",\"amount\":$BUDGET_USD,\"timeGrain\":\"Monthly\",\"timePeriod\":{\"startDate\":\"$(date -u +%Y-%m-01)T00:00:00Z\"},\"notifications\":{\"a50\":{\"enabled\":true,\"operator\":\"GreaterThan\",\"threshold\":50,\"contactEmails\":[\"$EMAIL\"]},\"a90\":{\"enabled\":true,\"operator\":\"GreaterThan\",\"threshold\":90,\"contactEmails\":[\"$EMAIL\"]}}}}" || echo "budget not created (continuing)"
fi

echo "== 2. Entra app (personal + work accounts, v2 tokens) + secret + service principal"
APPID=$(az ad app list --display-name "$APIM-aad" --query '[0].appId' -o tsv)
[ -n "$APPID" ] || APPID=$(az ad app create --display-name "$APIM-aad" --sign-in-audience AzureADandPersonalMicrosoftAccount \
  --web-redirect-uris "https://authorization-manager.consent.azure-apim.net/redirect/apim/$APIM" --query appId -o tsv)
OBJ=$(az ad app show --id "$APPID" --query id -o tsv)
# v2 tokens must be set before/with the audience change for personal accounts
az rest --method PATCH --url "https://graph.microsoft.com/v1.0/applications/$OBJ" \
  --body '{"api":{"requestedAccessTokenVersion":2}}' -o none
az ad sp show --id "$APPID" -o none 2>/dev/null || az ad sp create --id "$APPID" -o none   # needed so the Foundry MI can get a token for this audience
SECRET=$(az ad app credential reset --id "$APPID" --years 1 --query password -o tsv)

echo "== 3. Bicep deploy (APIM, provider, connection, MCP API+policy, Foundry, project, model, MI connection)"
az deployment group create -g "$RG" -f "$HERE/main.bicep" -o none -p \
  apimName="$APIM" foundryName="$FDRY" publisherEmail="$EMAIL" appId="$APPID" appClientSecret="$SECRET" projectName="$PROJ" modelName="${MODEL_NAME:-gpt-4o}" modelVersion="${MODEL_VERSION:-2024-11-20}"

echo "== 4. Create the Foundry agent (data plane)"
TOK=$(az account get-access-token --resource https://ai.azure.com --query accessToken -o tsv)
GW=https://$APIM.azure-api.net
jq -n --arg u "$GW/mcp/" '{definition:{kind:"prompt",model:"gpt4o",instructions:"Use the whoami MCP tool when asked who is signed in.",tools:[{type:"mcp",server_label:"apimbroker",server_url:$u,require_approval:"never",project_connection_id:"apimmcp-mi"}]}}' > /tmp/agent-$SUF.json
curl -fsS -X POST "https://$FDRY.services.ai.azure.com/api/projects/$PROJ/agents/whoami-agent/versions?api-version=2025-11-15-preview" \
  -H "Authorization: Bearer $TOK" -H "Content-Type: application/json" -d @/tmp/agent-$SUF.json -o /dev/null && echo agent created

echo "== 5. Consent link (MANUAL: open in a browser signed in as the user whose Graph identity the broker should use)"
B="https://management.azure.com/subscriptions/$SUB/resourceGroups/$RG/providers/Microsoft.ApiManagement/service/$APIM/authorizationProviders/aadv2/authorizations/c2"
az rest --method post --url "$B/getLoginLinks?api-version=2022-08-01" --body '{"postLoginRedirectUrl":"https://www.example.com"}' --query loginLink -o tsv
echo "After consenting (lands on example.com), verify Connected:"
echo "  az rest --method get --url \"$B?api-version=2022-08-01\" --query properties.status -o tsv"
echo "Then run ./verify.sh $SUF (or see README.md). Teardown: ./teardown.sh $SUF"
