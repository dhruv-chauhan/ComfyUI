"""Azure-authenticated Fal jobs with durable, submit-once recovery."""
import hashlib
import json
import logging
import time
from pathlib import Path
from urllib.parse import quote, urljoin, urlsplit

import requests

SCOPE = 'https://cognitiveservices.azure.com/.default'
KLING_ROOT = '/providers/falai/v1/kling/v3/pro'


def save_json(path, value):
    temporary = path.with_suffix('.tmp')
    temporary.write_text(json.dumps(value, indent=2) + '\n')
    temporary.replace(path)


class FoundryClient:
    def __init__(self, endpoint, credential, session, route=KLING_ROOT):
        parsed = urlsplit(endpoint)
        if (parsed.scheme != 'https' or not parsed.hostname
                or not parsed.hostname.endswith('.services.ai.azure.com')
                or parsed.username or parsed.password or parsed.port not in (None, 443)
                or parsed.path not in ('', '/') or parsed.query or parsed.fragment):
            raise ValueError('Configure a Foundry resource HTTPS endpoint, without a project path.')
        self.endpoint = endpoint.rstrip('/')
        self.origin = urlsplit(self.endpoint).netloc
        self.route = route
        self.root = self.endpoint + route
        self.credential = credential
        self.session = session

    def headers(self, url):
        if urlsplit(url).netloc == self.origin:
            return {'Authorization': 'Bearer ' + self.credential.get_token(SCOPE).token}
        return {}

    def api(self, method, path, payload=None):
        url = self.root + path
        try:
            with self.session.request(method, url, json=payload, headers=self.headers(url),
                                      allow_redirects=False, timeout=(10, 60)) as response:
                if response.status_code not in (200, 201, 202):
                    raise RuntimeError(f'Foundry returned HTTP {response.status_code}. Check the deployment and runtime role.')
                return response.json()
        except requests.RequestException:
            raise RuntimeError('Foundry connection failed. Retry the same inputs to recover the existing take.') from None

    def download(self, url, destination, check_interrupt):
        for _ in range(6):
            check_interrupt()
            parsed = urlsplit(url)
            if parsed.scheme != 'https' or not parsed.hostname or parsed.username or parsed.password:
                raise ValueError('Foundry returned an invalid video URL.')
            try:
                with self.session.get(url, headers=self.headers(url), stream=True,
                                      allow_redirects=False, timeout=(10, 120)) as response:
                    if response.status_code in (301, 302, 303, 307, 308):
                        location = response.headers.get('Location')
                        if not location:
                            raise RuntimeError('Video redirect had no destination.')
                        url = urljoin(url, location)
                        continue
                    if response.status_code != 200:
                        raise RuntimeError(f'Video download returned HTTP {response.status_code}. Retry the same take.')
                    temporary = destination.with_suffix('.mp4.part')
                    with temporary.open('wb') as output:
                        for chunk in response.iter_content(1024 * 1024):
                            check_interrupt()
                            output.write(chunk)
                    if temporary.stat().st_size == 0:
                        raise RuntimeError('Foundry returned an empty video.')
                    temporary.replace(destination)
                    return
            except requests.RequestException:
                raise RuntimeError('Video download interrupted. Retry the same take; no new job is needed.') from None
        raise RuntimeError('Too many video redirects.')

    def generate(self, payload, take, journal_root, check_interrupt=lambda: None,
                 progress=lambda stage: None, timeout=1200, submit_path='/text-to-video'):
        # Preserve the original Kling take IDs, including completed paid jobs.
        route = self.route if submit_path == '/text-to-video' else self.route + submit_path
        spec = {'endpoint': self.endpoint, 'route': route, 'payload': payload, 'take': take}
        run_id = hashlib.sha256(json.dumps(spec, sort_keys=True).encode()).hexdigest()
        directory = Path(journal_root) / run_id
        directory.parent.mkdir(parents=True, exist_ok=True)
        manifest_path = directory / 'job.json'
        video = directory / 'source.mp4'
        check_interrupt()
        try:
            directory.mkdir()
        except FileExistsError:
            if not manifest_path.is_file():
                raise RuntimeError(f'Take {take} has an incomplete submission record. Inspect job {run_id}; no new request was sent.')
            manifest = json.loads(manifest_path.read_text())
            if not manifest.get('request_id'):
                raise RuntimeError(f'Take {take} has an uncertain submission. Inspect job {run_id} before starting a new take.')
        else:
            manifest = {**spec, 'status': 'SUBMITTING'}
            save_json(manifest_path, manifest)
            # Never retry POST: a lost response may still represent a paid job.
            receipt = self.api('POST', submit_path, payload)
            save_json(directory / 'receipt.json', receipt)
            request_id = receipt.get('request_id')
            if not isinstance(request_id, str) or not request_id:
                raise RuntimeError('Foundry returned no job ID. Inspect the submission record before making a new take.')
            manifest.update(request_id=request_id, status='SUBMITTED')
            save_json(manifest_path, manifest)

        request_id = manifest['request_id']
        logging.info('Foundry %s take %s, request %s', payload['model'], take, request_id)
        if video.is_file() and manifest.get('status') == 'DOWNLOADED':
            progress(4)
            return video, request_id

        result_path = '/requests/' + quote(request_id, safe='')
        deadline = time.monotonic() + timeout
        last_state = None
        while time.monotonic() < deadline:
            check_interrupt()
            status = self.api('GET', result_path + '/status')
            state = str(status.get('status', 'UNKNOWN')).upper()
            manifest['status'] = state
            save_json(manifest_path, manifest)
            if state != last_state:
                logging.info('Foundry request %s: %s', request_id, state)
                last_state = state
            if state == 'COMPLETED':
                break
            if state not in ('IN_QUEUE', 'IN_PROGRESS', 'QUEUED', 'PENDING', 'RUNNING'):
                raise RuntimeError(f'Foundry job {request_id} stopped with {state}. A new take requires changing the take number.')
            progress(2 if state in ('IN_PROGRESS', 'RUNNING') else 1)
            for _ in range(10):
                check_interrupt()
                time.sleep(1)
        else:
            raise TimeoutError(f'Foundry job {request_id} is still remote. Run the same inputs to resume; do not change the take number.')

        progress(3)
        result = self.api('GET', result_path)
        save_json(directory / 'result.json', result)
        self.download(result['video']['url'], video, check_interrupt)
        manifest['status'] = 'DOWNLOADED'
        save_json(manifest_path, manifest)
        progress(4)
        return video, request_id
