"""Durable single submissions for Foundry's synchronous image and speech APIs."""
import base64
import hashlib
import json
from pathlib import Path
from urllib.parse import urlsplit

import requests

SCOPE = 'https://cognitiveservices.azure.com/.default'


def write_bytes(path, content):
    temporary = path.with_suffix(path.suffix + '.part')
    temporary.write_bytes(content)
    temporary.replace(path)


def cache_spec(endpoint, path, payload, take, extension, files=(), ssml=None, speech_resource_id=None):
    return {'endpoint': endpoint, 'path': path, 'payload': payload, 'take': take, 'extension': extension,
            'files': [(name, hashlib.sha256(content).hexdigest()) for name, content in files],
            'ssml': ssml, 'speech_resource_id': speech_resource_id}


def cache_directory(root, spec):
    return Path(root) / hashlib.sha256(json.dumps(spec, sort_keys=True).encode()).hexdigest()


class MediaClient:
    def __init__(self, endpoint, credential, session, speech_resource_id=None):
        parsed = urlsplit(endpoint)
        if (parsed.scheme != 'https' or not parsed.hostname
                or not parsed.hostname.endswith(('.services.ai.azure.com', '.tts.speech.microsoft.com'))
                or parsed.username or parsed.password or parsed.port not in (None, 443)
                or parsed.path not in ('', '/') or parsed.query or parsed.fragment):
            raise ValueError('Configure an Azure Foundry or Speech HTTPS resource endpoint.')
        self.endpoint = endpoint.rstrip('/')
        self.credential = credential
        self.session = session
        self.speech_resource_id = speech_resource_id

    def generate(self, path, payload, take, root, extension='png', files=(), ssml=None,
                 check_interrupt=lambda: None):
        if extension not in ('png', 'wav'):
            raise ValueError('Unsupported media format.')
        spec = cache_spec(self.endpoint, path, payload, take, extension, files, ssml, self.speech_resource_id)
        directory = cache_directory(root, spec)
        directory.parent.mkdir(parents=True, exist_ok=True)
        source = directory / ('source.' + extension)
        response_path = directory / 'response.json'
        check_interrupt()
        try:
            directory.mkdir()
        except FileExistsError:
            if source.is_file() and source.stat().st_size:
                return source
            if not response_path.is_file():
                raise RuntimeError('This take has an uncertain or rejected submission. Inspect its media journal before changing take; no request was repeated.')
        else:
            write_bytes(directory / 'request.json', json.dumps(spec).encode())
            token = self.credential.get_token(SCOPE).token
            if self.speech_resource_id:
                token = 'aad#' + self.speech_resource_id + '#' + token
            headers = {'Authorization': 'Bearer ' + token}
            if ssml is not None:
                headers.update({'Content-Type': 'application/ssml+xml',
                                'X-Microsoft-OutputFormat': 'riff-24khz-16bit-mono-pcm'})
                request = {'data': ssml.encode()}
            elif files:
                request = {'data': payload, 'files': [(name, (f'reference-{i}.png', content, 'image/png'))
                                                    for i, (name, content) in enumerate(files)]}
            else:
                request = {'json': payload}
            try:
                # Never retry a POST: a lost response can still represent a charge.
                with self.session.post(self.endpoint + path, headers=headers, allow_redirects=False,
                                       timeout=(10, 300), **request) as response:
                    if response.status_code != 200:
                        raise RuntimeError(f'Foundry media returned HTTP {response.status_code}. Check model access and parameters; no automatic retry was sent.')
                    if extension == 'wav':
                        if not response.content:
                            raise RuntimeError('Foundry returned empty audio.')
                        write_bytes(source, response.content)
                    else:
                        write_bytes(response_path, json.dumps(response.json()).encode())
            except requests.RequestException:
                raise RuntimeError('Foundry media request interrupted. Inspect this take before starting another; the service may have accepted it.') from None
        # Keep a successful response even if the user interrupts local decoding.
        check_interrupt()
        if extension == 'png':
            result = json.loads(response_path.read_text())
            encoded = result['data'][0]['b64_json']
            write_bytes(source, base64.b64decode(encoded, validate=True))
        return source
