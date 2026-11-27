#!/bin/sh
set -eu
mkdir -p /data/input /data/output /data/user
exec python main.py --cpu --listen 0.0.0.0 --port 8188 \
  --disable-auto-launch --disable-partner-nodes --disable-assets \
  --input-directory /data/input --output-directory /data/output \
  --user-directory /data/user --temp-directory /tmp/comfyui
