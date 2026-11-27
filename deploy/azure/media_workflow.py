"""Install image/speech workflows, or explicitly submit one paid generation."""
import argparse
import importlib.util
import io
import json
import logging
import time
import urllib.error
import urllib.request
import wave
from pathlib import Path

from PIL import Image

from foundry_workflow import BASE, api

MODELS = {
    'sunburst': ('FoundryGPTImageSunburst', 'gpt-image-2.5-sunburst'),
    'flare': ('FoundryGPTImageFlare', 'gpt-image-2.5-flare'),
    'mai-image': ('FoundryMAIImage', 'MAI-Image-2.7'),
    'mai-image-flash': ('FoundryMAIImageFlash', 'MAI-Image-2.7-Flash'),
    'flux-pro': ('FoundryFluxPro', 'FLUX.2-pro'),
    'flux-flex': ('FoundryFluxFlex', 'FLUX.2-flex'),
    'gpt-speech': ('FoundryGPTSpeech', 'gpt-4o-mini-tts'),
    'mai-voice': ('FoundryMAIVoice', 'MAI-Voice-2.1'),
    'mai-voice-flash': ('FoundryMAIVoiceFlash', 'MAI-Voice-2.1-Flash'),
}


def is_audio(model):
    return model == 'gpt-speech' or model.startswith('mai-voice')


def inputs(model):
    if is_audio(model):
        values = {'text': 'Every great story begins with a moment of wonder.'}
        if model == 'gpt-speech':
            values.update(instructions='Speak warmly, with a calm cinematic narrator voice.', voice='coral')
        else:
            values.update(voice='en-US-Harper', style='neutral')
    else:
        values = {'prompt': 'A cinematic still of an ivory ceramic cup beside a sunlit window, warm light, shallow depth of field, no text.'}
        if model in ('sunburst', 'flare'):
            values.update(size='1024x1024', quality='medium', background='auto')
        else:
            values.update(width=1024, height=1024)
            if model.startswith('mai-image'):
                values['transparent_background'] = False
            elif model == 'flux-flex':
                values.update(guidance=4.5, steps=50)
    return {**values, 'take': 1}


def graph(model, image=None):
    audio = is_audio(model)
    result = {'1': {'class_type': MODELS[model][0], 'inputs': inputs(model)},
              '2': {'class_type': 'SaveAudioAdvanced' if audio else 'SaveImage', 'inputs': {
                  'audio' if audio else 'images': ['1', 0], 'filename_prefix': 'foundry/' + model,
                  **({'format': 'flac'} if audio else {})}}}
    if image is not None:
        if audio:
            raise ValueError('Speech workflows do not accept reference images.')
        result['3'] = {'class_type': 'LoadImage', 'inputs': {'image': image}}
        result['1']['inputs']['images'] = ['3', 0]
    return result


def workflow(model, image=None):
    audio = is_audio(model)
    kind = 'AUDIO' if audio else 'IMAGE'
    node, save = MODELS[model][0], 'SaveAudioAdvanced' if audio else 'SaveImage'
    result = {'last_node_id': 2, 'last_link_id': 1, 'version': 0.4, 'groups': [], 'config': {}, 'extra': {},
              'nodes': [
                  {'id': 1, 'type': node, 'pos': [400, 100], 'size': [450, 430], 'flags': {}, 'order': 0, 'mode': 0,
                   'inputs': [] if audio else [{'name': 'images', 'type': 'IMAGE', 'link': 2 if image is not None else None}],
                   'outputs': [{'name': 'audio' if audio else 'image', 'type': kind, 'links': [1]}],
                   'properties': {'Node name for S&R': node}, 'widgets_values': list(inputs(model).values())},
                  {'id': 2, 'type': save, 'pos': [950, 100], 'size': [420, 430], 'flags': {}, 'order': 1, 'mode': 0,
                   'inputs': [{'name': 'audio' if audio else 'images', 'type': kind, 'link': 1}],
                   'outputs': [{'name': 'audio', 'type': 'AUDIO', 'links': None}] if audio else [],
                   'properties': {'Node name for S&R': save}, 'widgets_values': ['foundry/' + model] + (['flac'] if audio else [])}],
              'links': [[1, 1, 0, 2, 0, kind]]}
    if image is not None:
        result.update(last_node_id=3, last_link_id=2)
        result['nodes'][0]['order'] = 1
        result['nodes'][1]['order'] = 2
        result['nodes'].append({'id': 3, 'type': 'LoadImage', 'pos': [20, 100], 'size': [320, 330],
                                'flags': {}, 'order': 0, 'mode': 0, 'inputs': [],
                                'outputs': [{'name': 'IMAGE', 'type': 'IMAGE', 'links': [2]}, {'name': 'MASK', 'type': 'MASK', 'links': None}],
                                'properties': {'Node name for S&R': 'LoadImage'}, 'widgets_values': [image, 'image']})
        result['links'].append([2, 3, 0, 1, 0, 'IMAGE'])
    return result


def install():
    for model in MODELS:
        for image in ([None] if is_audio(model) else [None, '']):
            suffix = 'speech' if is_audio(model) else ('text-to-image' if image is None else 'image-edit')
            path = '/userdata/workflows%2Ffoundry-' + model + '-' + suffix + '.json'
            request = urllib.request.Request(BASE + path + '?overwrite=false', data=json.dumps(workflow(model, image)).encode(),
                                             headers={'Content-Type': 'application/json'})
            try:
                with urllib.request.urlopen(request) as response:
                    response.read()
            except urllib.error.HTTPError as error:
                if error.code != 409:
                    raise
            assert api(path)['version'] == 0.4
    logging.info('PASS: Foundry media workflows installed (15 starters; existing edits preserved)')


def load_module(name):
    spec = importlib.util.spec_from_file_location(name, Path(__file__).resolve().parents[2] / 'custom_nodes/azure_foundry' / (name + '.py'))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def fixture_test(model, image=None):
    client, requests = load_module('media_client'), load_module('media_requests')
    graph_data = graph(model, image)
    settings = dict(inputs(model))
    take = settings.pop('take')
    ssml = resource = None
    files = []
    if is_audio(model):
        extension = 'wav'
        if model == 'gpt-speech':
            endpoint, path = requests.AUDIO_ENDPOINT, '/openai/v1/audio/speech'
            payload = {'model': MODELS[model][1], 'input': settings['text'], 'instructions': settings['instructions'],
                       'voice': settings['voice'], 'response_format': 'wav'}
        else:
            endpoint, path, payload = 'https://eastus.tts.speech.microsoft.com', '/cognitiveservices/v1', {}
            ssml = requests.speech_ssml(MODELS[model][1], settings['voice'], settings['text'], settings['style'])
            resource = requests.SPEECH_RESOURCE_ID
        buffer = io.BytesIO()
        with wave.open(buffer, 'wb') as output:
            output.setparams((1, 2, 24000, 0, 'NONE', 'not compressed'))
            output.writeframes(b'\x00\x00\x00\x10' * 12000)
    else:
        extension = 'png'
        prompt = settings.pop('prompt')
        if model.startswith('mai-image'):
            settings.pop('transparent_background')
        refs = []
        if image is not None:
            reference = io.BytesIO()
            Image.new('RGB', (64, 64), (0, 0, 255)).save(reference, format='PNG')
            (Path('/data/input') / image).write_bytes(reference.getvalue())
            refs.append(reference.getvalue())
        endpoint, path, payload, files = requests.image_request(MODELS[model][1], prompt, settings, refs)
        buffer = io.BytesIO()
        Image.new('RGBA', (64, 64), (0, 0, 255, 128)).save(buffer, format='PNG')
    spec = client.cache_spec(endpoint, path, payload, take, extension, files, ssml, resource)
    directory = client.cache_directory('/data/user/__foundry/media', spec)
    directory.mkdir(parents=True, exist_ok=True)
    (directory / ('source.' + extension)).write_bytes(buffer.getvalue())
    receipt = api('/prompt', {'prompt': graph_data})
    deadline = time.monotonic() + 90
    while time.monotonic() < deadline:
        history = api('/history/' + receipt['prompt_id'])
        if receipt['prompt_id'] in history:
            result = history[receipt['prompt_id']]
            assert result['status']['status_str'] == 'success', result['status']
            output = result['outputs']['2']['audio' if is_audio(model) else 'images'][0]
            saved = Path('/data/output') / output['subfolder'] / output['filename']
            assert saved.stat().st_size > 0
            if not is_audio(model):
                with Image.open(saved) as decoded:
                    assert decoded.size == (64, 64) and decoded.getpixel((0, 0)) == (0, 0, 255, 128)
            logging.info('PASS: %s %s → native save, without a provider call', model, 'edit' if image else 'text')
            return
        time.sleep(1)
    raise TimeoutError('Media fixture timed out')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    action = parser.add_mutually_exclusive_group(required=True)
    action.add_argument('--install', action='store_true')
    action.add_argument('--fixture-test', action='store_true')
    action.add_argument('--submit', action='store_true')
    parser.add_argument('--model', choices=MODELS, default='flare')
    parser.add_argument('--image')
    args = parser.parse_args()
    if args.install:
        install()
    elif args.fixture_test:
        for model in MODELS:
            fixture_test(model)
            if not is_audio(model):
                fixture_test(model, 'foundry-media-fixture.png')
    else:
        logging.info(json.dumps(api('/prompt', {'prompt': graph(args.model, args.image),
                                              'extra_data': {'extra_pnginfo': {'workflow': workflow(args.model, args.image)}}})))


if __name__ == '__main__':
    logging.basicConfig(level=logging.INFO, format='%(message)s')
    main()
