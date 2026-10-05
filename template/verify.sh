#!/usr/bin/env bash
# Usage: ./verify.sh <suffix>. Checks after consent: Connected, agent answers, negative cases.
set -uo pipefail; export PYTHONWARNINGS=ignore
SUF=${1:?suffix}; RG=rg-apim-cm-$SUF; APIM=apim-cm-$SUF; FDRY=foundry-cm-$SUF; GW=https://$APIM.azure-api.net
SUB=$(az account show --query id -o tsv); pass=0; fail=0
ck(){ if [ "$2" = "$3" ]; then echo "PASS $1"; pass=$((pass+1)); else echo "FAIL $1 (got '$2', want '$3')"; fail=$((fail+1)); fi; }
B="https://management.azure.com/subscriptions/$SUB/resourceGroups/$RG/providers/Microsoft.ApiManagement/service/$APIM/authorizationProviders/aadv2/authorizations/c2"
ck "connection c2 Connected" "$(az rest --method get --url "$B?api-version=2022-08-01" --query properties.status -o tsv)" Connected
ck "no token -> 401" "$(curl -s -o /dev/null -w '%{http_code}' -X POST $GW/mcp/ -H 'Content-Type: application/json' -d '{"jsonrpc":"2.0","id":1,"method":"tools/list"}')" 401
ck "garbage token -> 401" "$(curl -s -o /dev/null -w '%{http_code}' -X POST $GW/mcp/ -H 'Authorization: Bearer abc' -H 'Content-Type: application/json' -d '{"jsonrpc":"2.0","id":1,"method":"tools/list"}')" 401
T=$(az account get-access-token --resource https://ai.azure.com --query accessToken -o tsv)
OUT=$(curl -s -m 150 -X POST "https://$FDRY.services.ai.azure.com/api/projects/proj1/openai/responses?api-version=2025-11-15-preview" -H "Authorization: Bearer $T" -H "Content-Type: application/json" -d '{"input":"Use your tool to tell me who the signed-in user is.","agent_reference":{"name":"whoami-agent","type":"agent_reference"}}')
echo "$OUT" | grep -q "Signed-in user (via APIM broker)" && R=yes || R=no
ck "agent mcp_call returns signed-in user" "$R" yes
echo "passed=$pass failed=$fail"; [ $fail -eq 0 ]
