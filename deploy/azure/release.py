"""Release a tested image to an idle shared studio, preserving authentication."""
import argparse
import json
import logging
import os
import pty
import select
import subprocess
import time

from bootstrap import az


def remote(group, command):
    master, slave = pty.openpty()
    process = subprocess.Popen(
        ['az', 'containerapp', 'exec', '-g', group, '-n', 'comfyui-dev', '--command', command],
        stdin=slave, stdout=slave, stderr=slave,
    )
    os.close(slave)
    chunks = []
    deadline = time.monotonic() + 240
    try:
        while time.monotonic() < deadline:
            ready, _, _ = select.select([master], [], [], 1)
            if ready:
                try:
                    chunk = os.read(master, 65536)
                except OSError:
                    break
                if not chunk:
                    break
                chunks.append(chunk)
            elif process.poll() is not None:
                break
        else:
            raise TimeoutError('Remote validation timed out')
    finally:
        os.close(master)
        if process.poll() is None:
            process.terminate()
        process.wait(timeout=10)
    output = b''.join(chunks).decode(errors='replace')
    logging.info(output)
    return output


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--registry', required=True)
    parser.add_argument('--tag', required=True)
    parser.add_argument('--resource-group', default='rg-comfyui-dev')
    args = parser.parse_args()
    group = args.resource_group
    check = ('python -c "import json,urllib.request; '
             "q=json.load(urllib.request.urlopen('http://127.0.0.1:8188/queue')); "
             "assert not q['queue_running'] and not q['queue_pending'], 'Studio is busy'; "
             "print('COMFYUI_QUEUE_IDLE')\"")
    if 'COMFYUI_QUEUE_IDLE' not in [line.strip() for line in remote(group, check).splitlines()]:
        raise RuntimeError('Could not verify an empty queue; release aborted.')
    current = az('containerapp', 'show', '-g', group, '-n', 'comfyui-dev')
    old_image = current['properties']['template']['containers'][0]['image']
    foundation = az('deployment', 'group', 'show', '-g', group, '-n', 'comfyui-foundation', '--query', 'properties.outputs')
    storage = foundation['storageName']['value']
    keys = az('storage', 'account', 'keys', 'list', '-g', group, '-n', storage)
    snapshot_result = subprocess.run(
        ['az', 'storage', 'share', 'snapshot', '--account-name', storage, '--name', 'comfyui', '-o', 'json', '--only-show-errors'],
        env={**os.environ, 'AZURE_STORAGE_KEY': keys[0]['value']}, capture_output=True, text=True, check=True,
    )
    snapshot = json.loads(snapshot_result.stdout)['snapshot']
    digest = az('acr', 'repository', 'show', '-n', args.registry, '--image', 'comfyui:' + args.tag, '--query', 'digest')
    image = args.registry + '.azurecr.io/comfyui@' + digest
    logging.info(json.dumps({'previousImage': old_image, 'image': image, 'storageSnapshot': snapshot}, indent=2))
    az('containerapp', 'update', '-g', group, '-n', 'comfyui-dev', '--image', image)
    output = remote(group, 'python deploy/azure/smoke_test.py --verify-persistence')
    if 'PASS: input, output, workflow, and settings survived replacement' not in output:
        az('containerapp', 'update', '-g', group, '-n', 'comfyui-dev', '--image', old_image)
        raise RuntimeError('Persistence validation failed; restored previous image. Check the studio before submitting jobs.')
    output = remote(group, 'python deploy/azure/foundry_workflow.py --install')
    if 'PASS: Foundry starter workflow installed' not in output:
        raise RuntimeError('Image released, but starter workflow installation failed. Install it before generating video.')
    output = remote(group, 'python deploy/azure/media_workflow.py --install')
    if 'PASS: Foundry media workflows installed' not in output:
        raise RuntimeError('Image released, but media workflow installation failed.')
    logging.info('Release complete. The original Entra configuration is unchanged.')


if __name__ == '__main__':
    logging.basicConfig(level=logging.INFO, format='%(message)s')
    main()
