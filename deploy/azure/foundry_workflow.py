"""Install the starter workflow; submit a paid test only with --submit."""
import argparse
import hashlib
import base64
import io
import importlib.util
import json
import logging
import os
import subprocess
import time
import urllib.error
import urllib.request
from pathlib import Path

from PIL import Image

BASE = 'http://127.0.0.1:8188'
NODE_IDS = {
    'kling': 'FoundryKlingTextToVideo',
    'ltx-fast': 'FoundryLTXFastVideo', 'ltx-pro': 'FoundryLTXProVideo',
    'minimax-max': 'FoundryMiniMaxVideo', 'minimax-turbo': 'FoundryMiniMaxTurboVideo',
}
PROMPT = (
    'A single continuous cinematic close-up of a handmade ivory ceramic cup of hot tea on a dark wooden table '
    'beside a window. Delicate wisps of steam rise naturally in warm morning sunlight. The camera slowly pushes '
    'closer, with a softly blurred green garden in the background. Photorealistic materials, gentle natural motion, '
    'calm atmosphere. Keep the cup shape stable. No people, text, logos, cuts, or sudden movement.'
)


def api(path, payload=None):
    request = urllib.request.Request(BASE + path, data=None if payload is None else json.dumps(payload).encode(),
                                     headers={'Content-Type': 'application/json'})
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            return json.load(response)
    except urllib.error.HTTPError as error:
        raise RuntimeError(f'ComfyUI HTTP {error.code}: {error.read().decode()[:2000]}') from None


def inputs(prompt=PROMPT, model='kling'):
    if model == 'kling':
        return {'prompt': prompt, 'duration': '5', 'aspect_ratio': '16:9', 'generate_audio': False, 'take': 1}
    if model.startswith('ltx-'):
        return {'prompt': prompt, 'duration': '6', 'resolution': '720p', 'aspect_ratio': '16:9',
                'fps': '25', 'generate_audio': True, 'camera_motion': 'prompt', 'take': 1}
    return {'prompt': prompt, 'duration': 5.0, 'resolution': '768P', 'aspect_ratio': '16:9',
            'prompt_expansion_mode': 'disabled', 'take': 1}


def graph(prompt=PROMPT, model='kling', image=None):
    result = {
        '1': {'class_type': NODE_IDS[model], 'inputs': inputs(prompt, model)},
        '2': {'class_type': 'SaveVideo', 'inputs': {
            'video': ['1', 0], 'filename_prefix': 'foundry/' + model, 'format': 'mp4', 'format.codec': 'auto',
        }},
    }

    if image is not None:
        result['3'] = {'class_type': 'LoadImage', 'inputs': {'image': image}}
        result['1']['inputs']['image'] = ['3', 0]
    return result


def workflow(model='kling', image=None):
    result = {
        'last_node_id': 2, 'last_link_id': 1, 'version': 0.4,
        'nodes': [
            {'id': 1, 'type': NODE_IDS[model], 'pos': [50, 100], 'size': [480, 430],
             'flags': {}, 'order': 0, 'mode': 0, 'inputs': [],
             'outputs': [{'name': 'video', 'type': 'VIDEO', 'links': [1]},
                         {'name': 'request_id', 'type': 'STRING', 'links': None}],
             'properties': {'Node name for S&R': NODE_IDS[model]},
             'widgets_values': list(inputs(model=model).values())},
            {'id': 2, 'type': 'SaveVideo', 'pos': [650, 100], 'size': [480, 430],
             'flags': {}, 'order': 1, 'mode': 0,
             'inputs': [{'name': 'video', 'type': 'VIDEO', 'link': 1}],
             'outputs': [{'name': 'video', 'type': 'VIDEO', 'links': None}],
             'properties': {'Node name for S&R': 'SaveVideo'},
             'widgets_values': ['foundry/' + model, 'mp4', 'auto', 'auto']},
        ],
        'links': [[1, 1, 0, 2, 0, 'VIDEO']], 'groups': [], 'config': {}, 'extra': {},
    }

    if model != 'kling':
        result['nodes'][0]['inputs'] = [
            {'name': 'image', 'type': 'IMAGE', 'link': 2 if image is not None else None},
            {'name': 'end_image', 'type': 'IMAGE', 'link': None},
        ]
    if image is not None:
        result.update(last_node_id=3, last_link_id=2)
        result['nodes'][0]['pos'] = [400, 100]
        result['nodes'][0]['order'] = 1
        result['nodes'][1]['pos'] = [950, 100]
        result['nodes'][1]['order'] = 2
        result['nodes'].append({
            'id': 3, 'type': 'LoadImage', 'pos': [20, 100], 'size': [320, 330],
            'flags': {}, 'order': 0, 'mode': 0, 'inputs': [],
            'outputs': [{'name': 'IMAGE', 'type': 'IMAGE', 'links': [2]}, {'name': 'MASK', 'type': 'MASK', 'links': None}],
            'properties': {'Node name for S&R': 'LoadImage'}, 'widgets_values': [image, 'image'],
        })
        result['links'].append([2, 3, 0, 1, 0, 'IMAGE'])
    return result


def install():
    for model in NODE_IDS:
        for image in ([None] if model == 'kling' else [None, '']):
            suffix = 'first-video' if model == 'kling' else ('text-to-video' if image is None else 'image-to-video')
            path = f'/userdata/workflows%2Ffoundry-{model}-{suffix}.json'
            request = urllib.request.Request(BASE + path + '?overwrite=false',
                                             data=json.dumps(workflow(model, image)).encode(),
                                             headers={'Content-Type': 'application/json'})
            try:
                with urllib.request.urlopen(request) as response:
                    response.read()
            except urllib.error.HTTPError as error:
                if error.code != 409:
                    raise
            assert api(path)['version'] == 0.4
    logging.info('PASS: Foundry starter workflow installed (all five models; existing edits preserved)')


def fixture_test(model='kling', image=None):
    # Seed a completed take with a synthetic clip. The real node and SaveVideo
    # must execute without asking Azure for a token or submitting a paid job.
    prompt = 'CI fixture: blue video; never submit this prompt to a provider.'
    payload = inputs(prompt, model)
    take = payload.pop('take')
    if model == 'kling':
        payload['model'] = os.environ['FOUNDRY_VIDEO_DEPLOYMENT']
        route = '/providers/falai/v1/kling/v3/pro'
    else:
        image_url = None
        if image:
            # Identical PNG bytes to LoadImage -> image_data_uri for this fixture.
            buffer = io.BytesIO()
            Image.new('RGB', (64, 64), (0, 0, 255)).save(buffer, format='PNG')
            (Path('/data/input') / image).write_bytes(buffer.getvalue())
            image_url = 'data:image/png;base64,' + base64.b64encode(buffer.getvalue()).decode()
        # Import request-only code without loading ComfyUI's GPU-oriented defaults.
        module_spec = importlib.util.spec_from_file_location('foundry_models', Path(__file__).resolve().parents[2] / 'custom_nodes/azure_foundry/models.py')
        module = importlib.util.module_from_spec(module_spec)
        module_spec.loader.exec_module(module)
        route, operation, payload = module.prepare_request(model, payload, image_url)
        if operation != '/text-to-video':
            route += operation
    spec = {'endpoint': os.environ['FOUNDRY_VIDEO_ENDPOINT'], 'route': route, 'payload': payload, 'take': take}
    key = hashlib.sha256(json.dumps(spec, sort_keys=True).encode()).hexdigest()
    directory = Path('/data/user/__foundry/jobs') / key
    directory.mkdir(parents=True, exist_ok=True)
    subprocess.run(['ffmpeg', '-v', 'error', '-y', '-f', 'lavfi', '-i', 'color=c=blue:s=64x64:r=24',
                    '-f', 'lavfi', '-i', 'sine=frequency=440:sample_rate=44100',
                    '-t', '1', '-c:v', 'libx264', '-c:a', 'aac', '-pix_fmt', 'yuv420p', str(directory / 'source.mp4')], check=True)
    (directory / 'job.json').write_text(json.dumps({**spec, 'status': 'DOWNLOADED', 'request_id': 'ci-fixture'}))
    receipt = api('/prompt', {'prompt': graph(prompt, model, image)})
    deadline = time.monotonic() + 90
    while time.monotonic() < deadline:
        history = api('/history/' + receipt['prompt_id'])
        if receipt['prompt_id'] in history:
            result = history[receipt['prompt_id']]
            assert result['status']['status_str'] == 'success', result['status']
            output = result['outputs']['2']['images'][0]
            saved = Path('/data/output') / output['subfolder'] / output['filename']
            probe = json.loads(subprocess.check_output(['ffprobe', '-v', 'error', '-show_streams', '-of', 'json', str(saved)]))
            assert probe['streams'][0]['width'] == 64
            assert any(stream['codec_type'] == 'audio' for stream in probe['streams'])
            logging.info('PASS: %s %s → native SaveVideo, without a provider call', model, 'image' if image else 'text')
            return
        time.sleep(1)
    raise TimeoutError('Video fixture workflow timed out')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    action = parser.add_mutually_exclusive_group(required=True)
    action.add_argument('--install', action='store_true')
    action.add_argument('--fixture-test', action='store_true')
    action.add_argument('--submit', action='store_true', help='Run one paid take using the selected model')
    action.add_argument('--status', metavar='COMFY_PROMPT_ID')
    parser.add_argument('--model', choices=NODE_IDS, default='kling')
    parser.add_argument('--image', help='Existing input filename for image-to-video')
    args = parser.parse_args()
    if args.image and args.model == 'kling':
        parser.error('The existing Kling node is text-to-video only.')
    if args.install:
        install()
    elif args.fixture_test:
        for model in NODE_IDS:
            fixture_test(model)
            if model != 'kling':
                fixture_test(model, 'foundry-fixture.png')
    elif args.submit:
        receipt = api('/prompt', {'prompt': graph(model=args.model, image=args.image),
                                  'extra_data': {'extra_pnginfo': {'workflow': workflow(args.model, args.image)}}})
        logging.info(json.dumps(receipt))
    else:
        history = api('/history/' + args.status)
        if args.status not in history:
            logging.info(json.dumps({'state': 'pending', 'queue': api('/queue')}))
        else:
            record = history[args.status]
            logging.info(json.dumps({'status': record['status'], 'outputs': record.get('outputs', {})}))


if __name__ == '__main__':
    logging.basicConfig(level=logging.INFO, format='%(message)s')
    main()
