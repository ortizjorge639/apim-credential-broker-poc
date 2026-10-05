// APIM Credential Manager broker + Foundry agent caller (Approach A PoC).
// Prereq: Entra app registration + secret (see deploy.sh). Consent is a manual browser step.
targetScope = 'resourceGroup'

@description('Globally unique APIM name, e.g. apim-cm-<suffix>')
param apimName string
@description('Globally unique Foundry (AIServices) account name / custom subdomain')
param foundryName string
param location string = resourceGroup().location
param publisherEmail string
@description('Entra app (client) ID used by the broker provider; also the audience APIM validates')
param appId string
@secure()
param appClientSecret string
param tenantId string = tenant().tenantId
param projectName string = 'proj1'
param modelName string = 'gpt-4o'
param modelVersion string = '2024-11-20'

var providerId = 'aadv2'
var authorizationId = 'c2'

resource apim 'Microsoft.ApiManagement/service@2022-08-01' = {
  name: apimName
  location: location
  sku: { name: 'Consumption', capacity: 0 }
  identity: { type: 'SystemAssigned' }
  properties: { publisherEmail: publisherEmail, publisherName: 'apim-cm-poc' }
}

// Generic oauth2 provider with explicit v2 URLs. The built-in 'aad' provider looped on consent for MSA/guest users.
resource provider 'Microsoft.ApiManagement/service/authorizationProviders@2022-08-01' = {
  parent: apim
  name: providerId
  properties: {
    displayName: providerId
    identityProvider: 'oauth2'
    oauth2: {
      redirectUrl: 'https://authorization-manager.consent.azure-apim.net/redirect/apim/${apimName}'
      grantTypes: {
        authorizationCode: {
          clientId: appId
          clientSecret: appClientSecret
          authorizationUrl: 'https://login.microsoftonline.com/common/oauth2/v2.0/authorize'
          tokenUrl: 'https://login.microsoftonline.com/common/oauth2/v2.0/token'
          refreshUrl: 'https://login.microsoftonline.com/common/oauth2/v2.0/token'
          scopes: 'https://graph.microsoft.com/User.Read offline_access'
        }
      }
    }
  }
}

// The connection is created unconsented; deploy.sh prints a login link to consent.
resource authorization 'Microsoft.ApiManagement/service/authorizationProviders/authorizations@2022-08-01' = {
  parent: provider
  name: authorizationId
  properties: { authorizationType: 'OAuth2', oauth2grantType: 'AuthorizationCode' }
}

resource accessPolicy 'Microsoft.ApiManagement/service/authorizationProviders/authorizations/accessPolicies@2022-08-01' = {
  parent: authorization
  name: 'apimmsi'
  properties: { objectId: apim.identity.principalId, tenantId: tenantId }
}

resource foundry 'Microsoft.CognitiveServices/accounts@2025-06-01' = {
  name: foundryName
  location: location
  kind: 'AIServices'
  sku: { name: 'S0' }
  identity: { type: 'SystemAssigned' }
  properties: { allowProjectManagement: true, customSubDomainName: foundryName, publicNetworkAccess: 'Enabled' }
}

resource project 'Microsoft.CognitiveServices/accounts/projects@2025-06-01' = {
  parent: foundry
  name: projectName
  location: location
  identity: { type: 'SystemAssigned' }
  properties: {}
}

resource model 'Microsoft.CognitiveServices/accounts/deployments@2025-06-01' = {
  parent: foundry
  name: 'gpt4o'
  // Serialize with the project: concurrent writes on one account return RequestConflict
  dependsOn: [ project ]
  sku: { name: 'GlobalStandard', capacity: 10 }
  properties: { model: { format: 'OpenAI', name: modelName, version: modelVersion } }
}

// Keyless Foundry -> APIM: project managed identity token for audience = appId.
resource mcpConn 'Microsoft.CognitiveServices/accounts/projects/connections@2025-06-01' = {
  parent: project
  name: 'apimmcp-mi'
  properties: {
    category: 'RemoteTool'
    target: '${apim.properties.gatewayUrl}/mcp/'
    authType: 'ProjectManagedIdentity'
    audience: appId
    isSharedToAll: true
    metadata: { type: 'custom_MCP' }
  }
}

resource mcpApi 'Microsoft.ApiManagement/service/apis@2022-08-01' = {
  parent: apim
  name: 'mcp'
  properties: {
    displayName: 'mcp'
    path: 'mcp'
    protocols: [ 'https' ]
    serviceUrl: 'https://example.com'
    subscriptionRequired: false
  }
}

resource mcpOp 'Microsoft.ApiManagement/service/apis/operations@2022-08-01' = {
  parent: mcpApi
  name: 'post'
  properties: { displayName: 'post', method: 'POST', urlTemplate: '/' }
}

// Policy pins the caller to this Foundry project's managed identity (oid claim).
var policy = replace(replace(replace(loadTextContent('mcp-policy.xml'), '{{TENANT_ID}}', tenantId), '{{APP_ID}}', appId), '{{CALLER_OID}}', project.identity.principalId)

resource mcpPolicy 'Microsoft.ApiManagement/service/apis/policies@2022-08-01' = {
  parent: mcpApi
  name: 'policy'
  dependsOn: [ authorization ]
  properties: { format: 'rawxml', value: policy }
}

output apimGatewayUrl string = apim.properties.gatewayUrl
output foundryProjectEndpoint string = 'https://${foundryName}.services.ai.azure.com/api/projects/${projectName}'
output projectPrincipalId string = project.identity.principalId
