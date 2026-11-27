targetScope = 'resourceGroup'
param runtimePrincipalId string

resource images 'Microsoft.CognitiveServices/accounts@2024-10-01' existing = {
  name: 'megastocks-illustrations-7bee'
}
resource video 'Microsoft.CognitiveServices/accounts@2024-10-01' existing = {
  name: 'megastocks-video-7bee'
}
resource flare 'Microsoft.CognitiveServices/accounts/deployments@2024-10-01' = {
  parent: images
  name: 'gpt-image-2.5-flare'
  sku: { name: 'GlobalStandard', capacity: 1 }
  properties: { model: { name: 'gpt-image-2.5-flare', format: 'OpenAI', version: '2026-09-08' } }
}
resource speech 'Microsoft.CognitiveServices/accounts/deployments@2024-10-01' = {
  parent: video
  name: 'gpt-4o-mini-tts'
  sku: { name: 'GlobalStandard', capacity: 1 }
  properties: { model: { name: 'gpt-4o-mini-tts', format: 'OpenAI', version: '2025-12-15' } }
}
var role = subscriptionResourceId('Microsoft.Authorization/roleDefinitions', 'a97b65f3-24c7-4388-baec-2e87135dc908')
resource imageInference 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  scope: images
  name: guid(images.id, runtimePrincipalId, role)
  properties: { roleDefinitionId: role, principalId: runtimePrincipalId, principalType: 'ServicePrincipal' }
}
// The video account already has this role through foundry.bicep.
