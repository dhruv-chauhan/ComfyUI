"""Exercise the local container without downloading or invoking a model."""
import argparse
import asyncio
import hashlib
import io
import json
import logging
import time
import uuid
from pathlib import Path

import aiohttp
from PIL import Image


async def check(base, verify_only, data_directory):
    manifest_path = data_directory / '.azure-smoke-test.json'
    async with aiohttp.ClientSession(raise_for_status=True) as session:
        async def get_json(path):
            async with session.get(base + path) as response:
                return await response.json()

        deadline = time.monotonic() + 180
        while True:
            try:
                await get_json('/system_stats')
                break
            except aiohttp.ClientConnectionError:
                if time.monotonic() >= deadline:
                    raise
                await asyncio.sleep(2)

        for node in ('FoundryKlingTextToVideo', 'FoundryLTXFastVideo', 'FoundryLTXProVideo',
                     'FoundryMiniMaxVideo', 'FoundryMiniMaxTurboVideo'):
            nodes = await get_json('/object_info/' + node)
            assert nodes[node]['output'] == ['VIDEO', 'STRING']
        if verify_only:
            manifest = json.loads(manifest_path.read_text())
            image = manifest['output']
            async with session.get(base + '/view', params={**image, 'type': 'output'}) as response:
                output = await response.read()
            assert hashlib.sha256(output).hexdigest() == manifest['sha256']
            workflow = await get_json('/userdata/workflows%2Fazure-smoke-test.json')
            assert workflow['version'] == 0.4
            assert await get_json('/api/userdata/workflows/azure-smoke-test.json') == workflow
            settings = await get_json('/settings')
            assert settings['Azure.SmokeTest'] == manifest['marker']
            assert (data_directory / 'input' / manifest['input']).is_file()
            logging.info('PASS: input, output, workflow, and settings survived replacement')
            return

        buffer = io.BytesIO()
        Image.new('RGB', (128, 96), (72, 140, 210)).save(buffer, format='PNG')
        marker = str(uuid.uuid4())
        form = aiohttp.FormData()
        form.add_field('image', buffer.getvalue(), filename=f'azure-smoke-{marker}.png', content_type='image/png')
        async with session.post(base + '/upload/image', data=form) as response:
            uploaded = await response.json()

        client_id = str(uuid.uuid4())
        graph = {
            '1': {'class_type': 'LoadImage', 'inputs': {'image': uploaded['name']}},
            '2': {'class_type': 'ImageScale', 'inputs': {
                'image': ['1', 0], 'upscale_method': 'nearest-exact', 'width': 64, 'height': 64, 'crop': 'disabled',
            }},
            '3': {'class_type': 'SaveImage', 'inputs': {'images': ['2', 0], 'filename_prefix': 'azure-smoke/test'}},
        }
        async with session.ws_connect(base + '/ws?clientId=' + client_id, timeout=30) as ws:
            first = await ws.receive_json(timeout=30)
            assert first['type'] == 'status'
            async with session.post(base + '/prompt', json={'prompt': graph, 'client_id': client_id}) as response:
                receipt = await response.json()
            request_id = receipt['prompt_id']
            deadline = time.monotonic() + 120
            while time.monotonic() < deadline:
                history = await get_json('/history/' + request_id)
                if request_id in history:
                    result = history[request_id]
                    assert result['status']['status_str'] == 'success', result['status']
                    break
                await asyncio.sleep(1)
            else:
                raise TimeoutError('Image workflow did not finish in 120 seconds')

        output_image = {key: result['outputs']['3']['images'][0][key] for key in ('filename', 'subfolder', 'type')}
        async with session.get(base + '/view', params=output_image) as response:
            output = await response.read()
        assert Image.open(io.BytesIO(output)).size == (64, 64)

        workflow = {
            'last_node_id': 3, 'last_link_id': 2, 'version': 0.4,
            'nodes': [
                {'id': 1, 'type': 'LoadImage', 'pos': [50, 150], 'size': [300, 310], 'flags': {}, 'order': 0, 'mode': 0,
                 'inputs': [], 'outputs': [{'name': 'IMAGE', 'type': 'IMAGE', 'links': [1]}, {'name': 'MASK', 'type': 'MASK', 'links': None}],
                 'properties': {'Node name for S&R': 'LoadImage'}, 'widgets_values': [uploaded['name'], 'image']},
                {'id': 2, 'type': 'ImageScale', 'pos': [400, 150], 'size': [300, 170], 'flags': {}, 'order': 1, 'mode': 0,
                 'inputs': [{'name': 'image', 'type': 'IMAGE', 'link': 1}], 'outputs': [{'name': 'IMAGE', 'type': 'IMAGE', 'links': [2]}],
                 'properties': {'Node name for S&R': 'ImageScale'}, 'widgets_values': ['nearest-exact', 64, 64, 'disabled']},
                {'id': 3, 'type': 'SaveImage', 'pos': [750, 150], 'size': [300, 310], 'flags': {}, 'order': 2, 'mode': 0,
                 'inputs': [{'name': 'images', 'type': 'IMAGE', 'link': 2}], 'outputs': [],
                 'properties': {'Node name for S&R': 'SaveImage'}, 'widgets_values': ['azure-smoke/test']},
            ],
            'links': [[1, 1, 0, 2, 0, 'IMAGE'], [2, 2, 0, 3, 0, 'IMAGE']], 'groups': [], 'config': {}, 'extra': {},
        }
        async with session.post(base + '/userdata/workflows%2Fazure-smoke-test.json?overwrite=true', json=workflow) as response:
            await response.read()
        assert await get_json('/api/userdata/workflows/azure-smoke-test.json') == workflow
        async with session.post(base + '/settings', json={'Azure.SmokeTest': marker}) as response:
            await response.read()
        manifest_path.write_text(json.dumps({
            'marker': marker, 'input': uploaded['name'], 'output': output_image,
            'sha256': hashlib.sha256(output).hexdigest(),
        }, indent=2))
        logging.info('PASS: WebSocket, upload, Load Image → Scale Image → Save Image, download, workflow, settings')


if __name__ == '__main__':
    logging.basicConfig(level=logging.INFO, format='%(message)s')
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--url', default='http://127.0.0.1:8188')
    parser.add_argument('--verify-persistence', action='store_true')
    parser.add_argument('--data-directory', type=Path, default=Path('/data'))
    args = parser.parse_args()
    asyncio.run(check(args.url.rstrip('/'), args.verify_persistence, args.data_directory))
