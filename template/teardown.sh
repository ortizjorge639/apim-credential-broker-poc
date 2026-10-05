#!/usr/bin/env bash
# Usage: ./teardown.sh <suffix>. Deletes the RG, purges the soft-deleted Foundry account and deletes the Entra app.
set -euo pipefail; export PYTHONWARNINGS=ignore
SUF=${1:?suffix}; RG=rg-apim-cm-$SUF
APPID=$(az ad app list --display-name "apim-cm-$SUF-aad" --query '[0].appId' -o tsv)
LOC=$(az group show -n "$RG" --query location -o tsv)
az group delete -n "$RG" --yes
[ -n "$APPID" ] && az ad app delete --id "$APPID"
az cognitiveservices account purge -g "$RG" -n "foundry-cm-$SUF" -l "$LOC" 2>/dev/null || true
az apim deletedservice purge --service-name "apim-cm-$SUF" --location "$LOC" 2>/dev/null || true
echo "Also revoke any GitHub OAuth app/authorization if you added the GitHub provider."
