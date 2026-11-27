#!/usr/bin/env bash
set -euo pipefail
image=${1:?Supply the image to test}
container="comfyui-test-${RANDOM}"
volume="${container}-data"
trap 'docker rm -f "$container" >/dev/null 2>&1 || true; docker volume rm "$volume" >/dev/null 2>&1 || true' EXIT
docker volume create "$volume" >/dev/null
docker run --rm --user root --entrypoint sh -v "$volume:/data" "$image" -c 'chown 1000:1000 /data'
docker run -d --name "$container" --cpus 2 --memory 4g -v "$volume:/data" "$image"
docker exec "$container" python deploy/azure/smoke_test.py
docker exec "$container" python deploy/azure/foundry_workflow.py --install
docker exec "$container" python deploy/azure/foundry_workflow.py --fixture-test
docker exec "$container" python -c 'import torch; assert torch.version.cuda is None; assert not torch.cuda.is_available()'
docker rm -f "$container"
docker run -d --name "$container" --cpus 2 --memory 4g -v "$volume:/data" "$image"
docker exec "$container" python deploy/azure/smoke_test.py --verify-persistence
docker exec "$container" python deploy/azure/foundry_workflow.py --fixture-test
docker logs "$container"
