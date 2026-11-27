"""Provider payloads kept independent of ComfyUI for request validation tests."""
import base64
import os
from xml.sax.saxutils import escape, quoteattr

MEDIA_ENDPOINT = 'https://comfyui-media-7bee.services.ai.azure.com'
GPT_IMAGE_ENDPOINT = 'https://megastocks-illustrations-7bee.services.ai.azure.com'
AUDIO_ENDPOINT = 'https://megastocks-video-7bee.services.ai.azure.com'
SPEECH_RESOURCE_ID = ('/subscriptions/7bee1889-43a0-4b3d-ad71-4b73f0c3db98/resourceGroups/rg-comfyui-dev/'
                      'providers/Microsoft.CognitiveServices/accounts/comfyui-media-7bee')


def image_request(model, prompt, settings, references=()):
    payload = {'model': model, 'prompt': prompt, **settings}
    files = []
    endpoint = os.environ.get('FOUNDRY_MEDIA_ENDPOINT', MEDIA_ENDPOINT)
    if model.startswith('gpt-image-'):
        endpoint = os.environ.get('FOUNDRY_GPT_IMAGE_ENDPOINT', GPT_IMAGE_ENDPOINT)
        path = '/openai/v1/images/' + ('edits' if references else 'generations')
        payload.update(n=1, output_format='png')
        files = [('image[]', content) for content in references]
    elif model.startswith('MAI-Image-'):
        path = '/mai/v1/images/' + ('edits' if references else 'generations')
        files = [('image', content) for content in references]
        if references:
            # The edits endpoint chooses its canvas from reference images.
            payload.pop('width')
            payload.pop('height')
    else:
        path = '/providers/blackforestlabs/v1/' + {'FLUX.2-pro': 'flux-2-pro', 'FLUX.2-flex': 'flux-2-flex'}[model] + '?api-version=preview'
        payload['output_format'] = 'png'
        for index, content in enumerate(references, 1):
            payload['input_image' + ('' if index == 1 else '_' + str(index))] = base64.b64encode(content).decode()
    return endpoint, path, payload, files


def speech_ssml(model, voice, text, style):
    # Free text is never interpreted as SSML markup.
    content = escape(text)
    if style != 'neutral':
        content = '<mstts:express-as style=' + quoteattr(style) + '>' + content + '</mstts:express-as>'
    return ('<speak version="1.0" xmlns="http://www.w3.org/2001/10/synthesis" '
            'xmlns:mstts="http://www.w3.org/2001/mstts" xml:lang="en-US"><voice name='
            + quoteattr(voice + ':' + model) + '>' + content + '</voice></speak>')
