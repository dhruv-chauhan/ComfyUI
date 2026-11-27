import importlib.util
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
import requests

spec = importlib.util.spec_from_file_location(
    'foundry_client', Path(__file__).resolve().parents[1] / 'custom_nodes/azure_foundry/client.py')
client_module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(client_module)
FoundryClient = client_module.FoundryClient

ENDPOINT = 'https://studio.services.ai.azure.com'
PAYLOAD = {'model': 'Fal-Kling-V3-Pro', 'prompt': 'A quiet sunrise', 'duration': '5'}


class Response:
    def __init__(self, body=None, status=200, headers=None, content=b'video'):
        self.body = body
        self.status_code = status
        self.headers = headers or {}
        self.content = content

    def __enter__(self):
        return self

    def __exit__(self, *args):
        pass

    def json(self):
        return self.body

    def iter_content(self, size):
        yield self.content


@pytest.fixture
def client():
    credential = Mock()
    credential.get_token.return_value = SimpleNamespace(token='test-token')
    return FoundryClient(ENDPOINT, credential, Mock())


def completed_responses():
    return [Response({'status': 'COMPLETED'}),
            Response({'video': {'url': ENDPOINT + '/assets/video.mp4'}})]


def test_completed_take_survives_new_client_without_resubmission(client, tmp_path):
    client.session.request.side_effect = [Response({'request_id': 'job-1'}), *completed_responses()]
    client.session.get.return_value = Response()
    path, request_id = client.generate(PAYLOAD, 1, tmp_path)
    assert request_id == 'job-1'
    assert path.read_bytes() == b'video'
    assert client.session.request.call_args_list[0].args[0] == 'POST'
    assert client.session.request.call_args_list[0].kwargs['allow_redirects'] is False
    fresh = FoundryClient(ENDPOINT, Mock(), Mock())
    assert fresh.generate(PAYLOAD, 1, tmp_path) == (path, 'job-1')
    fresh.session.request.assert_not_called()
    fresh.credential.get_token.assert_not_called()


def test_timeout_resumes_saved_request_without_post(client, tmp_path):
    client.session.request.return_value = Response({'request_id': 'job-2'})
    with pytest.raises(TimeoutError, match='Run the same inputs'):
        client.generate(PAYLOAD, 1, tmp_path, timeout=0)
    client.session.reset_mock()
    client.session.request.side_effect = completed_responses()
    client.session.get.return_value = Response()
    assert client.generate(PAYLOAD, 1, tmp_path)[1] == 'job-2'
    assert all(call.args[0] == 'GET' for call in client.session.request.call_args_list)


def test_uncertain_submission_is_never_retried(client, tmp_path):
    client.session.request.side_effect = requests.Timeout('private URL')
    with pytest.raises(RuntimeError, match='connection failed'):
        client.generate(PAYLOAD, 1, tmp_path)
    with pytest.raises(RuntimeError, match='uncertain submission'):
        client.generate(PAYLOAD, 1, tmp_path)
    assert client.session.request.call_count == 1


def test_incrementing_take_submits_a_new_job(client, tmp_path):
    client.session.request.side_effect = [Response({'request_id': 'first'}), *completed_responses(),
                                          Response({'request_id': 'second'}), *completed_responses()]
    client.session.get.return_value = Response()
    first = client.generate(PAYLOAD, 1, tmp_path)
    second = client.generate(PAYLOAD, 2, tmp_path)
    assert first[0] != second[0]
    assert [first[1], second[1]] == ['first', 'second']
    assert sum(call.args[0] == 'POST' for call in client.session.request.call_args_list) == 2


def test_cdn_redirect_does_not_receive_azure_token(client, tmp_path):
    client.session.get.side_effect = [Response(status=302, headers={'Location': 'https://cdn.example/video.mp4'}), Response()]
    client.download(ENDPOINT + '/assets/video.mp4', tmp_path / 'video.mp4', lambda: None)
    first, second = client.session.get.call_args_list
    assert first.kwargs['headers'] == {'Authorization': 'Bearer test-token'}
    assert second.kwargs['headers'] == {}
    assert second.kwargs['allow_redirects'] is False


def test_rejects_insecure_asset_redirect(client, tmp_path):
    client.session.get.return_value = Response(status=302, headers={'Location': 'http://cdn.example/video.mp4'})
    with pytest.raises(ValueError, match='invalid video URL'):
        client.download(ENDPOINT + '/assets/video.mp4', tmp_path / 'video.mp4', lambda: None)
    assert client.session.get.call_count == 1


def test_interrupted_download_keeps_job_for_retry(client, tmp_path):
    client.session.request.side_effect = [Response({'request_id': 'job-3'}), *completed_responses()]
    client.session.get.side_effect = requests.ConnectionError('signed URL must not leak')
    with pytest.raises(RuntimeError, match='download interrupted') as error:
        client.generate(PAYLOAD, 1, tmp_path)
    assert 'signed URL' not in str(error.value)
    client.session.reset_mock(side_effect=True)
    client.session.request.side_effect = completed_responses()
    client.session.get.return_value = Response()
    assert client.generate(PAYLOAD, 1, tmp_path)[1] == 'job-3'
    assert all(call.args[0] == 'GET' for call in client.session.request.call_args_list)


@pytest.mark.parametrize('endpoint', ['http://studio.services.ai.azure.com', 'https://evil.example',
                                     'https://studio.services.ai.azure.com/api/projects/test',
                                     'https://user:pass@studio.services.ai.azure.com'])
def test_rejects_untrusted_endpoint(endpoint):
    with pytest.raises(ValueError, match='Foundry resource HTTPS endpoint'):
        FoundryClient(endpoint, Mock(), Mock())


@pytest.mark.parametrize('root,operation', [
    ('lightricks/ltx-2.5', '/text-to-video/fast'),
    ('lightricks/ltx-2.5', '/image-to-video/pro'),
    ('minimax/h3-max', '/text-to-video'),
    ('minimax/h3-max-turbo', '/image-to-video'),
])
def test_model_routes_use_shared_job_root_without_operation_suffix(client, tmp_path, root, operation):
    route = '/providers/falai/v1/' + root
    routed = FoundryClient(ENDPOINT, client.credential, client.session, route)
    client.session.request.side_effect = [Response({'request_id': 'job-4'}), *completed_responses()]
    client.session.get.return_value = Response()
    routed.generate(PAYLOAD, 1, tmp_path, submit_path=operation)
    assert [(c.args[0], c.args[1]) for c in client.session.request.call_args_list] == [
        ('POST', ENDPOINT + route + operation),
        ('GET', ENDPOINT + route + '/requests/job-4/status'),
        ('GET', ENDPOINT + route + '/requests/job-4'),
    ]


def test_existing_kling_journal_still_reuses_original_cache_key(client, tmp_path):
    old_spec = {'endpoint': ENDPOINT, 'route': '/providers/falai/v1/kling/v3/pro', 'payload': PAYLOAD, 'take': 1}
    directory = tmp_path / hashlib.sha256(json.dumps(old_spec, sort_keys=True).encode()).hexdigest()
    directory.mkdir()
    (directory / 'source.mp4').write_bytes(b'original paid video')
    (directory / 'job.json').write_text(json.dumps({**old_spec, 'request_id': 'old-job', 'status': 'DOWNLOADED'}))
    assert client.generate(PAYLOAD, 1, tmp_path) == (directory / 'source.mp4', 'old-job')
    client.session.request.assert_not_called()


def test_modalities_and_ltx_variants_do_not_share_paid_job_cache(client, tmp_path):
    routed = FoundryClient(ENDPOINT, client.credential, client.session, '/providers/falai/v1/lightricks/ltx-2.5')
    client.session.request.side_effect = [r for _ in range(3) for r in
                                          [Response({'request_id': 'job'}), *completed_responses()]]
    client.session.get.return_value = Response()
    paths = [routed.generate(PAYLOAD, 1, tmp_path, submit_path=operation)[0] for operation in
             ('/text-to-video/fast', '/text-to-video/pro', '/image-to-video/pro')]
    assert len(set(paths)) == 3
