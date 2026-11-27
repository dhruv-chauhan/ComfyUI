import importlib.util
from pathlib import Path

import pytest

spec = importlib.util.spec_from_file_location(
    'foundry_models', Path(__file__).resolve().parents[1] / 'custom_nodes/azure_foundry/models.py')
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
prepare_request = module.prepare_request

LTX = {'prompt': 'A sunrise', 'duration': '6', 'resolution': '720p', 'aspect_ratio': '16:9',
       'fps': '25', 'generate_audio': True, 'camera_motion': 'prompt'}
MINIMAX = {'prompt': 'A sunrise', 'duration': 5, 'resolution': '768P',
           'aspect_ratio': '16:9', 'prompt_expansion_mode': 'disabled'}


@pytest.mark.parametrize('model,variant', [('ltx-fast', 'fast'), ('ltx-pro', 'pro')])
def test_ltx_api_types_and_routes(model, variant):
    root, operation, payload = prepare_request(model, LTX)
    assert root == '/providers/falai/v1/lightricks/ltx-2.5'
    assert operation == '/text-to-video/' + variant
    assert payload == {'model': 'Fal-LTX-2.5-' + variant.title(), 'prompt': 'A sunrise', 'duration': 6,
                       'resolution': '720p', 'aspect_ratio': '16:9', 'fps': 25, 'generate_audio': True}
    assert LTX['duration'] == '6'  # caller settings remain unchanged


@pytest.mark.parametrize('model,root,name', [
    ('minimax-max', 'minimax/h3-max', 'Fal-MiniMax-H3-Max'),
    ('minimax-turbo', 'minimax/h3-max-turbo', 'Fal-MiniMax-H3-Max-Turbo'),
])
def test_minimax_payload_uses_native_fields(model, root, name):
    route, operation, payload = prepare_request(model, MINIMAX)
    assert route == '/providers/falai/v1/' + root
    assert operation == '/text-to-video'
    assert payload == {'model': name, **MINIMAX}
    assert type(payload['duration']) is float


@pytest.mark.parametrize('model', ['ltx-fast', 'ltx-pro', 'minimax-max', 'minimax-turbo'])
def test_keyframes_change_modality_and_keep_native_payload(model):
    settings = LTX if model.startswith('ltx') else MINIMAX
    _, operation, payload = prepare_request(model, settings, 'data:image/png;base64,first', 'data:image/png;base64,last')
    assert operation.startswith('/image-to-video')
    assert payload['image_url'] == 'data:image/png;base64,first'
    assert payload['end_image_url'] == 'data:image/png;base64,last'
    assert ('aspect_ratio' in payload) == model.startswith('ltx')


def test_minimax_accepts_end_only_but_ltx_needs_start():
    assert prepare_request('minimax-max', MINIMAX, end_image_url='last')[1] == '/image-to-video'
    with pytest.raises(ValueError, match='start image'):
        prepare_request('ltx-fast', LTX, end_image_url='last')


@pytest.mark.parametrize('settings', [{'duration': '12', 'fps': '50'}, {'duration': '12', 'resolution': '2160p'}])
def test_ltx_rejects_unsupported_combinations_before_paid_submission(settings):
    with pytest.raises(ValueError, match='above 10 seconds'):
        prepare_request('ltx-fast', {**LTX, **settings})


def test_ltx_auto_and_camera_motion():
    _, _, payload = prepare_request('ltx-fast', {**LTX, 'duration': 'auto', 'camera_motion': 'dolly_in'})
    assert payload['duration'] == 'auto'
    assert payload['camera_motion'] == 'dolly_in'
    assert prepare_request('ltx-fast', {**LTX, 'duration': '20'})[2]['duration'] == 20


def test_deployment_name_override(monkeypatch):
    monkeypatch.setenv('FOUNDRY_MINIMAX_TURBO_DEPLOYMENT', 'my-turbo-deployment')
    assert prepare_request('minimax-turbo', MINIMAX)[2]['model'] == 'my-turbo-deployment'
