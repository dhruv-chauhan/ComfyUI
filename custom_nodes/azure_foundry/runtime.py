"""Run a video request with the studio identity and return ComfyUI's native VIDEO."""
import os
from pathlib import Path

import requests
from azure.identity import DefaultAzureCredential

import folder_paths
from comfy.model_management import throw_exception_if_processing_interrupted
from comfy.utils import ProgressBar
from comfy_api.latest import InputImpl, io

from .client import FoundryClient, KLING_ROOT


def generate_video(payload, take, route=KLING_ROOT, operation='/text-to-video'):
    progress = ProgressBar(4)
    with DefaultAzureCredential(managed_identity_client_id=os.environ.get('AZURE_CLIENT_ID')) as credential:
        with requests.Session() as session:
            client = FoundryClient(os.environ.get('FOUNDRY_VIDEO_ENDPOINT', ''), credential, session, route)
            video, request_id = client.generate(
                payload, take, Path(folder_paths.get_system_user_directory('foundry')) / 'jobs',
                check_interrupt=throw_exception_if_processing_interrupted,
                progress=progress.update_absolute, submit_path=operation,
            )
    return io.NodeOutput(InputImpl.VideoFromFile(str(video)), request_id)
