"""Request settings for the additional Fal models offered by Azure Foundry."""
import os

MODELS = {
    'ltx-fast': ('Fal-LTX-2.5-Fast', 'FOUNDRY_LTX_FAST_DEPLOYMENT', 'lightricks/ltx-2.5', 'fast'),
    'ltx-pro': ('Fal-LTX-2.5-Pro', 'FOUNDRY_LTX_PRO_DEPLOYMENT', 'lightricks/ltx-2.5', 'pro'),
    'minimax-max': ('Fal-MiniMax-H3-Max', 'FOUNDRY_MINIMAX_MAX_DEPLOYMENT', 'minimax/h3-max', ''),
    'minimax-turbo': ('Fal-MiniMax-H3-Max-Turbo', 'FOUNDRY_MINIMAX_TURBO_DEPLOYMENT', 'minimax/h3-max-turbo', ''),
}


def prepare_request(model, settings, image_url=None, end_image_url=None):
    name, variable, route, variant = MODELS[model]
    payload = {'model': os.environ.get(variable, name), **settings}
    is_image = image_url is not None or end_image_url is not None
    if variant:
        if end_image_url and not image_url:
            raise ValueError('LTX requires a start image when an end image is connected.')
        duration = payload['duration']
        payload['duration'] = 'auto' if duration == 'auto' else int(duration)
        payload['fps'] = int(payload['fps'])
        if (variant == 'fast' and duration != 'auto' and int(duration) > 10
                and (payload['fps'] > 25 or payload['resolution'] in ('1440p', '2160p'))):
            raise ValueError('LTX Fast durations above 10 seconds require 720p/1080p at 24/25 FPS.')
        if payload['camera_motion'] == 'prompt':
            del payload['camera_motion']
    else:
        payload['duration'] = float(payload['duration'])
        if is_image:
            # MiniMax takes its canvas from the keyframe; I2V has no aspect_ratio input.
            payload.pop('aspect_ratio')
    if image_url is not None:
        payload['image_url'] = image_url
    if end_image_url is not None:
        payload['end_image_url'] = end_image_url
    operation = '/image-to-video' if is_image else '/text-to-video'
    if variant:
        operation += '/' + variant
    return '/providers/falai/v1/' + route, operation, payload
