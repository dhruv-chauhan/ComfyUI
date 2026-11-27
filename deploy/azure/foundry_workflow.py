"""Install the starter workflow; submit a paid test only with --submit."""
import argparse
import hashlib
import json
import logging
import os
import subprocess
import time
import urllib.error
import urllib.request
from pathlib import Path

BASE = 'http://127.0.0.1:8188'
WORKFLOW_PATH = '/userdata/workflows%2Ffoundry-kling-first-video.json'
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


def inputs(prompt=PROMPT):
    return {'prompt': prompt, 'duration': '5', 'aspect_ratio': '16:9', 'generate_audio': False, 'take': 1}


def graph(prompt=PROMPT):
    return {
        '1': {'class_type': 'FoundryKlingTextToVideo', 'inputs': inputs(prompt)},
        '2': {'class_type': 'SaveVideo', 'inputs': {
            'video': ['1', 0], 'filename_prefix': 'foundry/kling', 'format': 'mp4', 'format.codec': 'auto',
        }},
    }


def workflow():
    return {
        'last_node_id': 2, 'last_link_id': 1, 'version': 0.4,
        'nodes': [
            {'id': 1, 'type': 'FoundryKlingTextToVideo', 'pos': [50, 100], 'size': [480, 430],
             'flags': {}, 'order': 0, 'mode': 0, 'inputs': [],
             'outputs': [{'name': 'video', 'type': 'VIDEO', 'links': [1]},
                         {'name': 'request_id', 'type': 'STRING', 'links': None}],
             'properties': {'Node name for S&R': 'FoundryKlingTextToVideo'},
             'widgets_values': list(inputs().values())},
            {'id': 2, 'type': 'SaveVideo', 'pos': [650, 100], 'size': [480, 430],
             'flags': {}, 'order': 1, 'mode': 0,
             'inputs': [{'name': 'video', 'type': 'VIDEO', 'link': 1}],
             'outputs': [{'name': 'video', 'type': 'VIDEO', 'links': None}],
             'properties': {'Node name for S&R': 'SaveVideo'},
             'widgets_values': ['foundry/kling', 'mp4', 'auto', 'auto']},
        ],
        'links': [[1, 1, 0, 2, 0, 'VIDEO']], 'groups': [], 'config': {}, 'extra': {},
    }


def install():
    request = urllib.request.Request(BASE + WORKFLOW_PATH + '?overwrite=false',
                                     data=json.dumps(workflow()).encode(), headers={'Content-Type': 'application/json'})
    try:
        with urllib.request.urlopen(request) as response:
            response.read()
    except urllib.error.HTTPError as error:
        if error.code != 409:
            raise
    assert api(WORKFLOW_PATH)['version'] == 0.4
    logging.info('PASS: Foundry starter workflow installed (existing edits preserved)')


def fixture_test():
    # Seed a completed take with a synthetic clip. The real node and SaveVideo
    # must execute without asking Azure for a token or submitting a paid job.
    prompt = 'CI fixture: blue video; never submit this prompt to a provider.'
    payload = {**inputs(prompt), 'model': os.environ['FOUNDRY_VIDEO_DEPLOYMENT']}
    take = payload.pop('take')
    spec = {'endpoint': os.environ['FOUNDRY_VIDEO_ENDPOINT'], 'route': '/providers/falai/v1/kling/v3/pro',
            'payload': payload, 'take': take}
    key = hashlib.sha256(json.dumps(spec, sort_keys=True).encode()).hexdigest()
    directory = Path('/data/user/__foundry/jobs') / key
    directory.mkdir(parents=True, exist_ok=True)
    subprocess.run(['ffmpeg', '-v', 'error', '-y', '-f', 'lavfi', '-i', 'color=c=blue:s=64x64:r=24',
                    '-t', '1', '-c:v', 'libx264', '-pix_fmt', 'yuv420p', str(directory / 'source.mp4')], check=True)
    (directory / 'job.json').write_text(json.dumps({**spec, 'status': 'DOWNLOADED', 'request_id': 'ci-fixture'}))
    receipt = api('/prompt', {'prompt': graph(prompt)})
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
            logging.info('PASS: Foundry cached take → VIDEO → native SaveVideo, without a provider call')
            return
        time.sleep(1)
    raise TimeoutError('Video fixture workflow timed out')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    action = parser.add_mutually_exclusive_group(required=True)
    action.add_argument('--install', action='store_true')
    action.add_argument('--fixture-test', action='store_true')
    action.add_argument('--submit', action='store_true', help='Run one five-second silent paid take')
    action.add_argument('--status', metavar='COMFY_PROMPT_ID')
    args = parser.parse_args()
    if args.install:
        install()
    elif args.fixture_test:
        fixture_test()
    elif args.submit:
        receipt = api('/prompt', {'prompt': graph(), 'extra_data': {'extra_pnginfo': {'workflow': workflow()}}})
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
