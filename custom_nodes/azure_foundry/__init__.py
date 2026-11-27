"""Foundry nodes use the server identity, independently of Comfy partner credits."""
import os

from comfy_api.latest import ComfyExtension, io

from .media_nodes import MEDIA_NODES
from .runtime import generate_video
from .video_nodes import VIDEO_NODES


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
        deployment = os.environ.get('FOUNDRY_VIDEO_DEPLOYMENT', '')
        if not deployment:
            raise ValueError('Set FOUNDRY_VIDEO_DEPLOYMENT on the ComfyUI server.')
        payload = {'model': deployment, 'prompt': prompt, 'duration': str(duration),
                   'aspect_ratio': aspect_ratio, 'generate_audio': generate_audio}
        return generate_video(payload, take)


class FoundryExtension(ComfyExtension):
    async def get_node_list(self):
        return [FoundryKlingTextToVideo, *VIDEO_NODES, *MEDIA_NODES]


async def comfy_entrypoint():
    return FoundryExtension()
