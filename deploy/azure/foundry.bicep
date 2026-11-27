targetScope = 'resourceGroup'

param accountName string = 'megastocks-video-7bee'
param runtimePrincipalId string

resource account 'Microsoft.CognitiveServices/accounts@2024-10-01' existing = {
  name: accountName
}

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
