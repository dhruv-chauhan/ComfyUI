"""Native image and audio nodes backed by the studio's Azure identity."""
import io as bytes_io
import os
from pathlib import Path

import numpy as np
import requests
import torch
from azure.identity import DefaultAzureCredential
from PIL import Image

import folder_paths
from comfy.model_management import throw_exception_if_processing_interrupted
from comfy_api.latest import io
from comfy_extras.nodes_audio import load as load_audio

from .media_client import MediaClient
from .media_requests import AUDIO_ENDPOINT, SPEECH_RESOURCE_ID, image_request, speech_ssml
from .video_nodes import take_input

DESCRIPTION = 'Uses Azure billing and the server identity. Identical inputs reuse a saved take; change take for another paid generation. '


def generate(endpoint, path, payload, take, extension='png', files=(), ssml=None, speech_resource_id=None):
    with DefaultAzureCredential(managed_identity_client_id=os.environ.get('AZURE_CLIENT_ID')) as credential:
        with requests.Session() as session:
            source = MediaClient(endpoint, credential, session, speech_resource_id).generate(
                path, payload, take, Path(folder_paths.get_system_user_directory('foundry')) / 'media',
                extension, files, ssml, throw_exception_if_processing_interrupted)
    if extension == 'wav':
        waveform, sample_rate = load_audio(str(source))
        return io.NodeOutput({'waveform': waveform.unsqueeze(0), 'sample_rate': sample_rate})
    with Image.open(source) as image:
        pixels = np.array(image.convert('RGBA' if 'A' in image.getbands() else 'RGB')).astype(np.float32) / 255
    return io.NodeOutput(torch.from_numpy(pixels).unsqueeze(0))


def generate_image(model, prompt, settings, take, images, limit):
    references = []
    if images is not None:
        if len(images) > limit:
            raise ValueError(f'{model} supports at most {limit} reference images; none were submitted.')
        for image in images:
            buffer = bytes_io.BytesIO()
            Image.fromarray((image.cpu().numpy().clip(0, 1) * 255).astype(np.uint8)).save(buffer, format='PNG')
            references.append(buffer.getvalue())
    endpoint, path, payload, files = image_request(model, prompt, settings, references)
    return generate(endpoint, path, payload, take, files=files)


def image_schema(cls, controls):
    return io.Schema(node_id=cls.__name__, display_name=cls.model + ' (Azure Foundry)',
                     category='Azure Foundry/image', description=DESCRIPTION + 'Connect images for reference-based editing. Output preserves PNG transparency.',
                     inputs=[io.String.Input('prompt', multiline=True, default='A cinematic still of a ceramic cup by a sunlit window, warm light, fine film grain.'),
                             *controls, take_input(), io.Image.Input('images', optional=True, tooltip='Optional reference image or image batch.')],
                     outputs=[io.Image.Output('image')])


class FoundryGPTImageSunburst(io.ComfyNode):
    model = 'gpt-image-2.5-sunburst'

    @classmethod
    def define_schema(cls):
        return image_schema(cls, [
            io.Combo.Input('size', options=['1024x1024', '1536x1024', '1024x1536', '2048x2048'], default='1024x1024'),
            io.Combo.Input('quality', options=['auto', 'low', 'medium', 'high', 'xhigh', 'max'], default='medium'),
            io.Combo.Input('background', options=['auto', 'opaque', 'transparent'], default='auto')])

    @classmethod
    def execute(cls, prompt, size, quality, background, take, images=None):
        return generate_image(cls.model, prompt, {'size': size, 'quality': quality, 'background': background}, take, images, 16)


class FoundryGPTImageFlare(FoundryGPTImageSunburst):
    model = 'gpt-image-2.5-flare'


class FoundryMAIImage(io.ComfyNode):
    model = 'MAI-Image-2.7'

    @classmethod
    def define_schema(cls):
        return image_schema(cls, [
            io.Int.Input('width', default=1024, min=768, max=2048, step=32, tooltip='Generation canvas; reference edits use the input canvas.'),
            io.Int.Input('height', default=1024, min=768, max=2048, step=32),
            io.Boolean.Input('transparent_background', default=False, tooltip='Text-to-image only; use the edit prompt for reference edits.')])

    @classmethod
    def execute(cls, prompt, width, height, transparent_background, take, images=None):
        if images is not None and transparent_background:
            raise ValueError('MAI transparent_background applies to text-to-image. For reference edits, describe the background in the prompt.')
        settings = {'width': width, 'height': height}
        if transparent_background:
            settings['background'] = 'transparent'
        return generate_image(cls.model, prompt, settings, take, images, 5)


class FoundryMAIImageFlash(FoundryMAIImage):
    model = 'MAI-Image-2.7-Flash'


class FoundryFluxPro(io.ComfyNode):
    model = 'FLUX.2-pro'

    @classmethod
    def define_schema(cls):
        controls = [io.Int.Input('width', default=1024, min=256, max=2048, step=32),
                    io.Int.Input('height', default=1024, min=256, max=2048, step=32)]
        if cls.model == 'FLUX.2-flex':
            controls += [io.Float.Input('guidance', default=4.5, min=1.5, max=10, step=0.1),
                         io.Int.Input('steps', default=50, min=1, max=50)]
        return image_schema(cls, controls)

    @classmethod
    def execute(cls, prompt, width, height, take, images=None, guidance=4.5, steps=50):
        settings = {'width': width, 'height': height}
        if cls.model == 'FLUX.2-flex':
            settings.update(guidance=guidance, steps=steps)
        return generate_image(cls.model, prompt, settings, take, images, 10 if cls.model == 'FLUX.2-flex' else 8)


class FoundryFluxFlex(FoundryFluxPro):
    model = 'FLUX.2-flex'


class FoundryGPTSpeech(io.ComfyNode):
    @classmethod
    def define_schema(cls):
        return io.Schema(node_id=cls.__name__, display_name='GPT-4o Mini TTS (Azure Foundry)', category='Azure Foundry/audio',
                         description=DESCRIPTION + 'AI-generated speech with voice-direction instructions.',
                         inputs=[io.String.Input('text', multiline=True, default='Every great story begins with a moment of wonder.'),
                                 io.String.Input('instructions', multiline=True, default='Speak warmly, with a calm cinematic narrator voice.'),
                                 io.Combo.Input('voice', options=['coral', 'alloy', 'ash', 'ballad', 'echo', 'fable', 'onyx', 'nova', 'sage', 'shimmer', 'verse', 'marin', 'cedar'], default='coral'),
                                 take_input()], outputs=[io.Audio.Output('audio')])

    @classmethod
    def execute(cls, text, instructions, voice, take):
        if len(text) > 4096:
            raise ValueError('GPT speech accepts up to 4096 characters per request. Split the narration into scenes.')
        return generate(os.environ.get('FOUNDRY_AUDIO_ENDPOINT', AUDIO_ENDPOINT), '/openai/v1/audio/speech',
                        {'model': 'gpt-4o-mini-tts', 'input': text, 'instructions': instructions, 'voice': voice, 'response_format': 'wav'}, take, 'wav')


class FoundryMAIVoice(io.ComfyNode):
    model = 'MAI-Voice-2.1'

    @classmethod
    def define_schema(cls):
        return io.Schema(node_id=cls.__name__, display_name=cls.model + ' (Azure Speech)', category='Azure Foundry/audio',
                         description=DESCRIPTION + 'AI-generated speech using prebuilt English voices.',
                         inputs=[io.String.Input('text', multiline=True, default='Every great story begins with a moment of wonder.'),
                                 io.Combo.Input('voice', options=['en-US-Harper', 'en-US-Ethan', 'en-US-Olivia'], default='en-US-Harper'),
                                 io.Combo.Input('style', options=['neutral', 'happy', 'sad', 'excited', 'whispering', 'softvoice'], default='neutral'),
                                 take_input()], outputs=[io.Audio.Output('audio')])

    @classmethod
    def execute(cls, text, voice, style, take):
        return generate(os.environ.get('FOUNDRY_SPEECH_ENDPOINT', 'https://eastus.tts.speech.microsoft.com'),
                        '/cognitiveservices/v1', {}, take, 'wav', ssml=speech_ssml(cls.model, voice, text, style),
                        speech_resource_id=os.environ.get('FOUNDRY_SPEECH_RESOURCE_ID', SPEECH_RESOURCE_ID))


class FoundryMAIVoiceFlash(FoundryMAIVoice):
    model = 'MAI-Voice-2.1-Flash'


MEDIA_NODES = [FoundryGPTImageSunburst, FoundryGPTImageFlare, FoundryMAIImage, FoundryMAIImageFlash,
               FoundryFluxPro, FoundryFluxFlex, FoundryGPTSpeech, FoundryMAIVoice, FoundryMAIVoiceFlash]
