targetScope = 'resourceGroup'

param runtimePrincipalId string
param accountName string = 'comfyui-media-7bee'

resource account 'Microsoft.CognitiveServices/accounts@2024-10-01' = {
  name: accountName
  location: 'eastus'
  kind: 'AIServices'
  sku: { name: 'S0' }
  properties: {
    customSubDomainName: accountName
    publicNetworkAccess: 'Enabled'
  }
}

var models = [
  { name: 'MAI-Image-2.7', format: 'Microsoft', version: '2026-11-02' }
  { name: 'MAI-Image-2.7-Flash', format: 'Microsoft', version: '2026-11-02' }
  { name: 'FLUX.2-pro', format: 'Black Forest Labs', version: '1' }
  { name: 'FLUX.2-flex', format: 'Black Forest Labs', version: '1' }
]
@batchSize(1)
resource deployments 'Microsoft.CognitiveServices/accounts/deployments@2024-10-01' = [for model in models: {
  parent: account
  name: model.name
  sku: { name: 'GlobalStandard', capacity: 1 }
  properties: { model: model }
}]

var role = subscriptionResourceId('Microsoft.Authorization/roleDefinitions', 'a97b65f3-24c7-4388-baec-2e87135dc908')
resource inference 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  scope: account
  name: guid(account.id, runtimePrincipalId, role)
  properties: { roleDefinitionId: role, principalId: runtimePrincipalId, principalType: 'ServicePrincipal' }
}

module existing 'media-existing.bicep' = {
  name: 'comfyui-existing-media'
  scope: resourceGroup('rg-megastocks-illustrations')
  params: { runtimePrincipalId: runtimePrincipalId }
}

output endpoint string = 'https://${accountName}.services.ai.azure.com'
output speechResourceId string = account.id
