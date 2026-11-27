FROM python:3.12-slim-bookworm@sha256:44cf1793efa7c7a476bb0f2cff81443f4a072c90c1cb127e2924933daa3e8c64

ENV PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1 HF_HUB_DISABLE_TELEMETRY=1 \
    DO_NOT_TRACK=1 OMP_NUM_THREADS=2 OPENBLAS_NUM_THREADS=2 \
    FOUNDRY_VIDEO_ENDPOINT=https://megastocks-video-7bee.services.ai.azure.com \
    FOUNDRY_VIDEO_DEPLOYMENT=Fal-Kling-V3-Pro
RUN apt-get update && apt-get install -y --no-install-recommends ffmpeg libgl1 libglib2.0-0 \
    && rm -rf /var/lib/apt/lists/* \
    && useradd --create-home --uid 1000 comfy
WORKDIR /opt/ComfyUI
COPY deploy/azure/requirements.lock /tmp/requirements.lock
RUN pip install --extra-index-url https://download.pytorch.org/whl/cpu -r /tmp/requirements.lock \
    && pip check
COPY --chown=comfy:comfy . .
RUN mkdir -p /data && chown comfy:comfy /data && chmod +x deploy/azure/start.sh
USER comfy
EXPOSE 8188
ENTRYPOINT ["/opt/ComfyUI/deploy/azure/start.sh"]
