targetScope = 'resourceGroup'

param location string = resourceGroup().location
param image string
param registryServer string
param environmentId string
param runtimeIdentityId string
param runtimeClientId string
param entraClientId string
param tenantId string = tenant().tenantId
@secure()
param entraClientSecret string
param publicIngress bool = false

resource app 'Microsoft.App/containerApps@2024-03-01' = {
  name: 'comfyui-dev'
  location: location
  identity: {
    type: 'UserAssigned'
    userAssignedIdentities: { '${runtimeIdentityId}': {} }
  }
  properties: {
    managedEnvironmentId: environmentId
    workloadProfileName: 'Consumption'
    configuration: {
      activeRevisionsMode: 'Single'
      ingress: {
        external: publicIngress
        targetPort: 8188
        transport: 'auto'
        allowInsecure: false
      }
      registries: [{ server: registryServer, identity: runtimeIdentityId }]
      secrets: [{ name: 'entra-client-secret', value: entraClientSecret }]
    }
    template: {
      scale: { minReplicas: 1, maxReplicas: 1 }
      containers: [{
        name: 'comfyui'
        image: image
        resources: { cpu: 2, memory: '4Gi' }
        env: [{ name: 'AZURE_CLIENT_ID', value: runtimeClientId }]
        volumeMounts: [{ volumeName: 'data', mountPath: '/data' }]
        probes: [
          { type: 'Startup', tcpSocket: { port: 8188 }, periodSeconds: 5, failureThreshold: 60 }
          { type: 'Readiness', httpGet: { path: '/system_stats', port: 8188 }, periodSeconds: 10, failureThreshold: 3 }
          { type: 'Liveness', tcpSocket: { port: 8188 }, periodSeconds: 30, failureThreshold: 5 }
        ]
      }]
      volumes: [{ name: 'data', storageType: 'AzureFile', storageName: 'comfyui-data' }]
    }
  }
}

resource auth 'Microsoft.App/containerApps/authConfigs@2024-03-01' = {
  parent: app
  name: 'current'
  properties: {
    platform: { enabled: true }
    globalValidation: {
      unauthenticatedClientAction: 'RedirectToLoginPage'
      redirectToProvider: 'AzureActiveDirectory'
      excludedPaths: []
    }
    httpSettings: { requireHttps: true }
    identityProviders: {
      azureActiveDirectory: {
        enabled: true
        registration: {
          clientId: entraClientId
          clientSecretSettingName: 'entra-client-secret'
          openIdIssuer: '${environment().authentication.loginEndpoint}${tenantId}/v2.0'
        }
        validation: { allowedAudiences: [entraClientId, 'api://${entraClientId}'] }
        login: { loginParameters: ['scope=openid profile email'] }
      }
    }
    login: { cookieExpiration: { convention: 'FixedTime', timeToExpiration: '08:00:00' } }
  }
}

output url string = 'https://${app.properties.configuration.ingress.fqdn}'
output appId string = app.id
