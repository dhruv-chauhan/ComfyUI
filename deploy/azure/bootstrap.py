"""Deploy the first private app, configure Entra, then publish its HTTPS endpoint.

Run foundation.bicep first. Azure CLI must be signed in as the bootstrap operator.
Secret values are captured in memory and a private, short-lived parameter file.
"""
import argparse
import json
import logging
import os
import subprocess
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

DIRECTORY = Path(__file__).resolve().parent


def az(*args):
    result = subprocess.run(['az', *args, '--only-show-errors', '-o', 'json'], capture_output=True, text=True)
    if result.returncode:
        raise RuntimeError(result.stderr.strip())
    return json.loads(result.stdout) if result.stdout.strip() else None


def deploy(group, parameters):
    with tempfile.NamedTemporaryFile(mode='w', suffix='.parameters.json') as file:
        os.chmod(file.name, 0o600)
        json.dump({'parameters': {key: {'value': value} for key, value in parameters.items()}}, file)
        file.flush()
        return az('deployment', 'group', 'create', '-g', group, '-n', 'comfyui-app',
                  '--template-file', str(DIRECTORY / 'app.bicep'), '--parameters', '@' + file.name,
                  '--query', 'properties.outputs')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--image', required=True, help='Immutable ACR image reference, including @sha256: digest')
    parser.add_argument('--resource-group', default='rg-comfyui-dev')
    args = parser.parse_args()
    if '@sha256:' not in args.image:
        parser.error('Use an immutable image digest.')
    group = args.resource_group
    outputs = az('deployment', 'group', 'show', '-g', group, '-n', 'comfyui-foundation', '--query', 'properties.outputs')
    foundation = {key: value['value'] for key, value in outputs.items()}
    apps = az('ad', 'app', 'list', '--display-name', 'comfyui-dev')
    if len(apps) > 1:
        raise RuntimeError('Multiple Entra apps named comfyui-dev; resolve the duplicate before bootstrap.')
    application = apps[0] if apps else az('ad', 'app', 'create', '--display-name', 'comfyui-dev',
                                        '--sign-in-audience', 'AzureADMyOrg', '--enable-id-token-issuance', 'true')
    service_principals = az('ad', 'sp', 'list', '--filter', f"appId eq '{application['appId']}'")
    if not service_principals:
        az('ad', 'sp', 'create', '--id', application['appId'])
    expiry = (datetime.now(timezone.utc) + timedelta(days=180)).strftime('%Y-%m-%dT%H:%M:%SZ')
    password = az('rest', '--method', 'POST', '--url',
                  f"https://graph.microsoft.com/v1.0/applications/{application['id']}/addPassword",
                  '--body', json.dumps({'passwordCredential': {'displayName': 'ComfyUI Easy Auth', 'endDateTime': expiry}}))
    state = {**foundation, 'resourceGroup': group, 'entraClientId': application['appId'],
             'entraObjectId': application['id'], 'credentialKeyId': password['keyId'], 'credentialExpiry': expiry}
    (DIRECTORY / '.state.json').write_text(json.dumps(state, indent=2) + '\n')
    parameters = {key: foundation[key] for key in ('registryServer', 'environmentId', 'runtimeIdentityId', 'runtimeClientId', 'tenantId')}
    parameters.update(image=args.image, entraClientId=application['appId'], entraClientSecret=password['secretText'], publicIngress=False)
    logging.info('Deploying private ComfyUI with authentication...')
    deploy(group, parameters)
    environment = az('containerapp', 'env', 'show', '-g', group, '-n', 'cae-comfyui-dev')
    public_url = 'https://comfyui-dev.' + environment['properties']['defaultDomain']
    az('ad', 'app', 'update', '--id', application['id'], '--web-redirect-uris', public_url + '/.auth/login/aad/callback')
    logging.info('Entra callback configured; enabling authenticated public ingress...')
    parameters['publicIngress'] = True
    result = deploy(group, parameters)
    state['url'] = result['url']['value']
    assert state['url'] == public_url, 'Unexpected public hostname; check the Entra redirect URI.'
    (DIRECTORY / '.state.json').write_text(json.dumps(state, indent=2) + '\n')
    logging.info(json.dumps({'url': state['url'], 'credentialExpiry': expiry}, indent=2))


if __name__ == '__main__':
    logging.basicConfig(level=logging.INFO, format='%(message)s')
    main()
