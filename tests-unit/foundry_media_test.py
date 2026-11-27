import base64
import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, Mock
from xml.etree import ElementTree

import pytest
import requests


def load(name):
    spec = importlib.util.spec_from_file_location(name, Path(__file__).resolve().parents[1] / 'custom_nodes/azure_foundry' / (name + '.py'))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


client = load('media_client')
payloads = load('media_requests')


def setup(response=None):
    credential = Mock()
    credential.get_token.return_value = SimpleNamespace(token='test-token')
    session = MagicMock()
    response = response or Mock(status_code=200)
    response.json.return_value = {'data': [{'b64_json': base64.b64encode(b'png-bytes').decode()}]}
    session.post.return_value.__enter__.return_value = response
    return client.MediaClient(payloads.MEDIA_ENDPOINT, credential, session), session, credential


def test_completed_take_reuses_bytes_without_token_or_post(tmp_path):
    media, session, credential = setup()
    first = media.generate('/images', {'prompt': 'cup'}, 1, tmp_path)
    assert first.read_bytes() == b'png-bytes'
    session.reset_mock()
    credential.reset_mock()
    assert media.generate('/images', {'prompt': 'cup'}, 1, tmp_path) == first
    session.post.assert_not_called()
    credential.get_token.assert_not_called()
    media.generate('/images', {'prompt': 'cup'}, 2, tmp_path)
    session.post.assert_called_once()


def test_lost_response_never_resubmits_or_exposes_credentials(tmp_path):
    media, session, _ = setup()
    session.post.side_effect = requests.Timeout('Authorization: test-token')
    with pytest.raises(RuntimeError, match='may have accepted') as error:
        media.generate('/images', {}, 1, tmp_path)
    assert 'test-token' not in str(error.value)
    with pytest.raises(RuntimeError, match='uncertain'):
        media.generate('/images', {}, 1, tmp_path)
    session.post.assert_called_once()


def test_interrupt_after_response_recovers_without_post(tmp_path):
    media, session, _ = setup()
    interrupt = Mock(side_effect=[None, RuntimeError('cancelled')])
    with pytest.raises(RuntimeError, match='cancelled'):
        media.generate('/images', {}, 1, tmp_path, check_interrupt=interrupt)
    assert media.generate('/images', {}, 1, tmp_path).read_bytes() == b'png-bytes'
    session.post.assert_called_once()


@pytest.mark.parametrize('status', [302, 400, 401, 429, 500])
def test_non_success_is_not_retried(tmp_path, status):
    media, session, _ = setup(Mock(status_code=status))
    with pytest.raises(RuntimeError, match=str(status)):
        media.generate('/images', {}, 1, tmp_path)
    assert session.post.call_args.kwargs['allow_redirects'] is False
    with pytest.raises(RuntimeError, match='uncertain'):
        media.generate('/images', {}, 1, tmp_path)
    session.post.assert_called_once()


def test_reference_content_participates_in_cache_identity(tmp_path):
    media, session, _ = setup()
    a = media.generate('/edits', {}, 1, tmp_path, files=[('image', b'first')])
    b = media.generate('/edits', {}, 1, tmp_path, files=[('image', b'second')])
    assert a != b and session.post.call_count == 2
    kwargs = session.post.call_args.kwargs
    assert kwargs['files'] == [('image', ('reference-0.png', b'second', 'image/png'))]
    assert kwargs['data'] == {}
    assert b'second' not in (b.parent / 'request.json').read_bytes()


def test_speech_uses_resource_scoped_entra_and_binary_output(tmp_path):
    media, session, _ = setup()
    media.speech_resource_id = '/resource'
    session.post.return_value.__enter__.return_value.content = b'RIFFaudio'
    result = media.generate('/cognitiveservices/v1', {}, 1, tmp_path, 'wav', ssml='<speak>hello</speak>')
    assert result.read_bytes() == b'RIFFaudio'
    kwargs = session.post.call_args.kwargs
    assert kwargs['headers']['Authorization'] == 'Bearer aad#/resource#test-token'
    assert kwargs['headers']['X-Microsoft-OutputFormat'] == 'riff-24khz-16bit-mono-pcm'
    assert kwargs['data'] == b'<speak>hello</speak>'
    assert 'test-token' not in json.dumps(json.loads((result.parent / 'request.json').read_text()))


@pytest.mark.parametrize('endpoint', ['http://a.services.ai.azure.com', 'https://example.com', 'https://a.services.ai.azure.com/path', 'https://a.services.ai.azure.com.evil.example', 'https://user@a.services.ai.azure.com'])
def test_reject_non_azure_endpoints(endpoint):
    with pytest.raises(ValueError):
        client.MediaClient(endpoint, Mock(), Mock())


@pytest.mark.parametrize('model,field', [('gpt-image-2.5-flare', 'image[]'), ('MAI-Image-2.7', 'image')])
def test_edits_use_provider_multipart_contract(model, field):
    settings = {'width': 1024, 'height': 1024} if model.startswith('MAI') else {'size': '1024x1024'}
    _, path, payload, files = payloads.image_request(model, 'edit cup', settings, [b'a', b'b'])
    assert path.endswith('/images/edits')
    assert files == [(field, b'a'), (field, b'b')]
    assert 'width' not in payload
    if model.startswith('MAI'):
        assert settings['width'] == 1024


@pytest.mark.parametrize('model', ['FLUX.2-pro', 'FLUX.2-flex'])
def test_flux_reference_bytes_use_native_json(model):
    _, path, payload, files = payloads.image_request(model, 'cup', {'width': 1024}, [b'a', b'b'])
    assert path.endswith(model.lower().replace('.', '-') + '?api-version=preview')
    assert payload['input_image'] == 'YQ==' and payload['input_image_2'] == 'Yg=='
    assert payload['output_format'] == 'png' and not files


def test_speech_escapes_text_without_inserting_markup():
    text = '<voice name="other">wrong</voice> & hello'
    ssml = payloads.speech_ssml('MAI-Voice-2.1', 'en-US-Harper', text, 'whispering')
    root = ElementTree.fromstring(ssml)
    assert ''.join(root.itertext()) == text
    assert root[0].attrib['name'] == 'en-US-Harper:MAI-Voice-2.1'
    assert root[0][0].attrib['style'] == 'whispering'
    assert len(root[0][0]) == 0
