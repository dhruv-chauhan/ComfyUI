targetScope = 'resourceGroup'

param accountName string = 'megastocks-video-7bee'
param runtimePrincipalId string

resource account 'Microsoft.CognitiveServices/accounts@2024-10-01' existing = {
  name: accountName
}

var videoModels = ['Fal-LTX-2.5-Fast', 'Fal-LTX-2.5-Pro', 'Fal-MiniMax-H3-Max', 'Fal-MiniMax-H3-Max-Turbo']

@batchSize(1)
resource deployments 'Microsoft.CognitiveServices/accounts/deployments@2024-10-01' = [for model in videoModels: {
  parent: account
  name: model
  sku: {
    name: 'GlobalStandard'
    capacity: 1
  }
  properties: {
    model: {
      format: 'fal.ai'
      name: model
      version: '2026-11-02'
    }
  }
}]

var cognitiveServicesUser = subscriptionResourceId('Microsoft.Authorization/roleDefinitions', 'a97b65f3-24c7-4388-baec-2e87135dc908')

resource inference 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  scope: account
  name: guid(account.id, runtimePrincipalId, cognitiveServicesUser)
  properties: {
    roleDefinitionId: cognitiveServicesUser
    principalId: runtimePrincipalId
    principalType: 'ServicePrincipal'
  }
}

output accountId string = account.id
