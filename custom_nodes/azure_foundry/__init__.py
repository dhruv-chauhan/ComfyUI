"""Foundry nodes use the server identity, independently of Comfy partner credits."""
import os
from pathlib import Path

import requests
from azure.identity import DefaultAzureCredential

import folder_paths
from comfy.model_management import throw_exception_if_processing_interrupted
from comfy.utils import ProgressBar
from comfy_api.latest import ComfyExtension, InputImpl, io

from .client import FoundryClient


class FoundryKlingTextToVideo(io.ComfyNode):
    @classmethod
    def define_schema(cls):
        return io.Schema(
            node_id='FoundryKlingTextToVideo',
            display_name='Kling Text to Video (Azure Foundry)',
            category='Azure Foundry/video',
            description='Generates video using the configured Azure deployment. Repeating identical inputs resumes or reuses the same take. Change take to generate a new paid result.',
            inputs=[
                io.String.Input('prompt', multiline=True, default='A cinematic landscape at sunrise, slow camera push-in, gentle natural motion.'),
                io.Combo.Input('duration', options=[str(n) for n in range(3, 16)], default='5'),
                io.Combo.Input('aspect_ratio', options=['16:9', '9:16', '1:1'], default='16:9'),
                io.Boolean.Input('generate_audio', default=False),
                io.Int.Input('take', default=1, min=1, max=2147483647,
                             tooltip='Keep this number to resume/reuse a take. Increment it for a new paid generation.'),
            ],
            outputs=[io.Video.Output('video'), io.String.Output('request_id')],
        )

    @classmethod
    def execute(cls, prompt, duration, aspect_ratio, generate_audio, take):
        endpoint = os.environ.get('FOUNDRY_VIDEO_ENDPOINT', '')
        deployment = os.environ.get('FOUNDRY_VIDEO_DEPLOYMENT', '')
        if not deployment:
            raise ValueError('Set FOUNDRY_VIDEO_DEPLOYMENT on the ComfyUI server.')
        payload = {'model': deployment, 'prompt': prompt, 'duration': str(duration),
                   'aspect_ratio': aspect_ratio, 'generate_audio': generate_audio}
        progress = ProgressBar(4)
        with DefaultAzureCredential(managed_identity_client_id=os.environ.get('AZURE_CLIENT_ID')) as credential:
            with requests.Session() as session:
                client = FoundryClient(endpoint, credential, session)
                video, request_id = client.generate(
                    payload, take, Path(folder_paths.get_system_user_directory('foundry')) / 'jobs',
                    check_interrupt=throw_exception_if_processing_interrupted,
                    progress=progress.update_absolute,
                )
        return io.NodeOutput(InputImpl.VideoFromFile(str(video)), request_id)


class FoundryExtension(ComfyExtension):
    async def get_node_list(self):
        return [FoundryKlingTextToVideo]


async def comfy_entrypoint():
    return FoundryExtension()
