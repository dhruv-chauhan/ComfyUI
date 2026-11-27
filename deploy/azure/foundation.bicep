targetScope = 'resourceGroup'

param location string = resourceGroup().location
param repository string = 'dhruv-chauhan/ComfyUI'

var suffix = uniqueString(resourceGroup().id)
var acrPull = subscriptionResourceId('Microsoft.Authorization/roleDefinitions', '7f951dda-4ed3-4680-a7ca-43fe172d538d')
var acrPush = subscriptionResourceId('Microsoft.Authorization/roleDefinitions', '8311e382-0749-4cb8-b61a-304f252e45ec')
var contributor = subscriptionResourceId('Microsoft.Authorization/roleDefinitions', 'b24988ac-6180-42a0-ab88-20f7382dd24c')

resource registry 'Microsoft.ContainerRegistry/registries@2023-07-01' = {
  name: 'comfyui${suffix}'
  location: location
  sku: { name: 'Basic' }
  properties: { adminUserEnabled: false }
}

resource storage 'Microsoft.Storage/storageAccounts@2023-05-01' = {
  name: 'comfyui${suffix}'
  location: location
  sku: { name: 'Standard_LRS' }
  kind: 'StorageV2'
  properties: {
    minimumTlsVersion: 'TLS1_2'
    supportsHttpsTrafficOnly: true
    allowBlobPublicAccess: false
  }
}

resource files 'Microsoft.Storage/storageAccounts/fileServices@2023-05-01' = {
  parent: storage
  name: 'default'
  properties: { shareDeleteRetentionPolicy: { enabled: true, days: 7 } }
}

resource share 'Microsoft.Storage/storageAccounts/fileServices/shares@2023-05-01' = {
  parent: files
  name: 'comfyui'
  properties: { shareQuota: 100, enabledProtocols: 'SMB', accessTier: 'TransactionOptimized' }
}

resource logs 'Microsoft.OperationalInsights/workspaces@2023-09-01' = {
  name: 'log-comfyui-dev'
  location: location
  properties: {
    sku: { name: 'PerGB2018' }
    retentionInDays: 30
  }
}

resource environment 'Microsoft.App/managedEnvironments@2024-03-01' = {
  name: 'cae-comfyui-dev'
  location: location
  properties: {
    workloadProfiles: [{ name: 'Consumption', workloadProfileType: 'Consumption' }]
    appLogsConfiguration: {
      destination: 'log-analytics'
      logAnalyticsConfiguration: {
        customerId: logs.properties.customerId
        sharedKey: logs.listKeys().primarySharedKey
      }
    }
  }
}

resource mount 'Microsoft.App/managedEnvironments/storages@2024-03-01' = {
  parent: environment
  name: 'comfyui-data'
  properties: {
    azureFile: {
      accountName: storage.name
      accountKey: storage.listKeys().keys[0].value
      shareName: share.name
      accessMode: 'ReadWrite'
    }
  }
}

resource runtime 'Microsoft.ManagedIdentity/userAssignedIdentities@2023-01-31' = {
  name: 'id-comfyui-runtime'
  location: location
}

resource pull 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  scope: registry
  name: guid(registry.id, runtime.id, acrPull)
  properties: { roleDefinitionId: acrPull, principalId: runtime.properties.principalId, principalType: 'ServicePrincipal' }
}

resource deployer 'Microsoft.ManagedIdentity/userAssignedIdentities@2023-01-31' = {
  name: 'id-comfyui-github'
  location: location
}

resource federation 'Microsoft.ManagedIdentity/userAssignedIdentities/federatedIdentityCredentials@2023-01-31' = {
  parent: deployer
  name: 'github-comfyui-dev'
  properties: {
    issuer: 'https://token.actions.githubusercontent.com'
    subject: 'repo:${repository}:environment:comfyui-dev'
    audiences: ['api://AzureADTokenExchange']
  }
}

resource deployRole 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(resourceGroup().id, deployer.id, contributor)
  properties: { roleDefinitionId: contributor, principalId: deployer.properties.principalId, principalType: 'ServicePrincipal' }
}

resource push 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  scope: registry
  name: guid(registry.id, deployer.id, acrPush)
  properties: { roleDefinitionId: acrPush, principalId: deployer.properties.principalId, principalType: 'ServicePrincipal' }
}

output registryName string = registry.name
output registryServer string = registry.properties.loginServer
output storageName string = storage.name
output environmentId string = environment.id
output runtimeIdentityId string = runtime.id
output runtimeClientId string = runtime.properties.clientId
output deployClientId string = deployer.properties.clientId
output tenantId string = tenant().tenantId
output subscriptionId string = subscription().subscriptionId
