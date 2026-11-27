"""LTX and MiniMax nodes with native ComfyUI image and video connections."""
import base64
import io as bytes_io

import numpy as np
from PIL import Image

from comfy_api.latest import io

from .models import prepare_request
from .runtime import generate_video

DESCRIPTION = ('Generates video through Azure Foundry. Connect image/end_image for keyframe animation, '
               'or leave them disconnected for text-to-video. Identical inputs reuse the same take; '
               'change take for a new paid generation.')


def image_data_uri(image):
    if image is None:
        return None
    if image.shape[0] != 1:
        raise ValueError('Connect one image per keyframe. Select a single image from the batch first.')
    pixels = np.clip(image[0].cpu().numpy() * 255, 0, 255).astype(np.uint8)
    buffer = bytes_io.BytesIO()
    Image.fromarray(pixels).save(buffer, format='PNG')
    return 'data:image/png;base64,' + base64.b64encode(buffer.getvalue()).decode('ascii')


def keyframe_inputs():
    return [
        io.Image.Input('image', optional=True, tooltip='Optional first frame from Load Image.'),
        io.Image.Input('end_image', optional=True, tooltip='Optional final frame from Load Image.'),
    ]


def take_input():
    return io.Int.Input('take', default=1, min=1, max=2147483647,
                        tooltip='Keep this number to resume/reuse a take. Increment it for a new paid generation.')


class FoundryLTXFastVideo(io.ComfyNode):
    model = 'ltx-fast'
    node_id = 'FoundryLTXFastVideo'
    title = 'LTX 2.5 Fast Video (Azure Foundry)'

    @classmethod
    def define_schema(cls):
        fast = cls.model == 'ltx-fast'
        return io.Schema(
            node_id=cls.node_id, display_name=cls.title, category='Azure Foundry/video', description=DESCRIPTION,
            inputs=[
                io.String.Input('prompt', multiline=True, default='A cinematic landscape at sunrise, slow camera push-in, gentle natural motion.'),
                io.Combo.Input('duration', options=[str(n) for n in range(6, 21 if fast else 11, 2)] + ['auto'], default='6'),
                io.Combo.Input('resolution', options=['720p', '1080p', '1440p', '2160p'] if fast else ['720p', '1080p'], default='720p'),
                io.Combo.Input('aspect_ratio', options=['16:9', '9:16'], default='16:9'),
                io.Combo.Input('fps', options=['24', '25', '48', '50'] if fast else ['24', '25', '50'], default='25'),
                io.Boolean.Input('generate_audio', default=True),
                io.Combo.Input('camera_motion', options=['prompt', 'dolly_in', 'dolly_out', 'dolly_left', 'dolly_right',
                                                        'jib_up', 'jib_down', 'static', 'focus_shift'], default='prompt',
                               tooltip='Choose prompt to leave camera movement to your description.'),
                take_input(), *keyframe_inputs(),
            ],
            outputs=[io.Video.Output('video'), io.String.Output('request_id')],
        )

    @classmethod
    def execute(cls, prompt, duration, resolution, aspect_ratio, fps, generate_audio, camera_motion, take,
                image=None, end_image=None):
        route, operation, payload = prepare_request(cls.model, {
            'prompt': prompt, 'duration': duration, 'resolution': resolution, 'aspect_ratio': aspect_ratio,
            'fps': fps, 'generate_audio': generate_audio, 'camera_motion': camera_motion,
        }, image_data_uri(image), image_data_uri(end_image))
        return generate_video(payload, take, route, operation)


class FoundryLTXProVideo(FoundryLTXFastVideo):
    model = 'ltx-pro'
    node_id = 'FoundryLTXProVideo'
    title = 'LTX 2.5 Pro Video (Azure Foundry)'


class FoundryMiniMaxVideo(io.ComfyNode):
    model = 'minimax-max'
    node_id = 'FoundryMiniMaxVideo'
    title = 'MiniMax H3 Max Video (Azure Foundry)'

    @classmethod
    def define_schema(cls):
        return io.Schema(
            node_id=cls.node_id, display_name=cls.title, category='Azure Foundry/video',
            description=DESCRIPTION + ' MiniMax generates audio; it has no audio-off control. Aspect ratio applies to text-to-video only.',
            inputs=[
                io.String.Input('prompt', multiline=True, default='A cinematic landscape at sunrise, slow camera push-in, gentle natural motion.'),
                io.Float.Input('duration', default=5, min=0.92, max=15, step=0.01),
                io.Combo.Input('resolution', options=['480P', '768P', '1080P'], default='768P'),
                io.Combo.Input('aspect_ratio', options=['21:9', '16:9', '4:3', '1:1', '3:4', '9:16'], default='16:9',
                               tooltip='Text-to-video only. When images are connected, the image determines the canvas.'),
                io.Combo.Input('prompt_expansion_mode', options=['disabled', 'balanced', 'quality'], default='disabled',
                               tooltip='Disabled follows your prompt directly. Balanced/quality ask the model to expand it.'),
                take_input(), *keyframe_inputs(),
            ],
            outputs=[io.Video.Output('video'), io.String.Output('request_id')],
        )

    @classmethod
    def execute(cls, prompt, duration, resolution, aspect_ratio, prompt_expansion_mode, take,
                image=None, end_image=None):
        route, operation, payload = prepare_request(cls.model, {
            'prompt': prompt, 'duration': duration, 'resolution': resolution,
            'aspect_ratio': aspect_ratio, 'prompt_expansion_mode': prompt_expansion_mode,
        }, image_data_uri(image), image_data_uri(end_image))
        return generate_video(payload, take, route, operation)


class FoundryMiniMaxTurboVideo(FoundryMiniMaxVideo):
    model = 'minimax-turbo'
    node_id = 'FoundryMiniMaxTurboVideo'
    title = 'MiniMax H3 Max Turbo Video (Azure Foundry)'


VIDEO_NODES = [FoundryLTXFastVideo, FoundryLTXProVideo, FoundryMiniMaxVideo, FoundryMiniMaxTurboVideo]
