# ComfyUI on Azure

One shared, CPU-only studio in `rg-comfyui-dev`, East US 2. All users authenticated
through the configured Entra tenant can access the same queue, settings,
workflows, uploads, and outputs. Tenant guests are included. This is not a
per-user isolation boundary.

## Architecture

Container Apps provides HTTPS, WebSockets, and Entra Easy Auth. ComfyUI listens
on port 8188 with one 2-vCPU/4-GiB replica. The runtime identity pulls from ACR;
it also authenticates to the existing Foundry video resource. Azure Files is mounted at `/data`:

- `/data/input`: uploaded source media.
- `/data/output`: saved results.
- `/data/user`: workflows and settings.

Temporary processing files are ephemeral. The in-memory queue and history do
not survive a restart. The optional SQLite asset catalog is disabled because
this deployment uses a network file share. Media generation and normal workflow
saving do not require that catalog.

User-data routes accept nested paths after Azure ingress decodes URL-encoded
slashes. This small server compatibility fix preserves ComfyUI's existing
path-containment and download-response protections.

No GPU, model weights, Manager, or built-in paid partner nodes are enabled.
Package custom nodes into the image and pin their dependencies. Keep future
Foundry integrations in custom nodes rather than modifying ComfyUI core.

## Bootstrap

Requires Azure CLI, Bicep, a subscription Owner for infrastructure/role setup,
and permission to create an Entra application. Use the intended subscription:

```sh
az account set --subscription 7bee1889-43a0-4b3d-ad71-4b73f0c3db98
az group create -n rg-comfyui-dev -l eastus2
az deployment group create -g rg-comfyui-dev -n comfyui-foundation \
  --template-file deploy/azure/foundation.bicep
```

Build and push this checkout with `deploy/azure/Dockerfile`, then resolve its
ACR digest and run:

```sh
python deploy/azure/bootstrap.py --image '<registry>.azurecr.io/comfyui@sha256:<digest>'
```

Bootstrap creates/reuses the `comfyui-dev` Entra application, creates a 180-day
sign-in credential, deploys the app with internal ingress and authentication,
sets the public callback, and finally enables public ingress. Secret values
exist only in memory and a mode-0600 temporary ARM parameter file that is removed
after use. `.state.json` contains identifiers and expiry, not credentials, and
is gitignored. Bootstrap is for initial provisioning, not routine releases.
An interrupted retry may leave an unused Entra credential; remove it after
checking which key is active.

The SMB storage key is configured directly on the Container Apps environment
through ARM `listKeys()`. It is not injected into the ComfyUI container. Never
commit parameter files, tokens, or keys.

## GitHub releases

The `Azure ComfyUI` workflow builds and runs a model-free smoke test on pull
requests. Manual releases from `master` publish the tested image, snapshot the
file share, verify an empty queue, update the image, and verify persistent data.
A persistence failure restores the previous image. Release during an agreed
idle window: the empty-queue check is not a lock against another user submitting
new work concurrently. Do not deploy during generation.

Configure GitHub environment `comfyui-dev` to allow only `master`, with these
nonsecret variables from the foundation deployment outputs:

| Variable | Output |
| --- | --- |
| `AZURE_CLIENT_ID` | `deployClientId` |
| `AZURE_TENANT_ID` | `tenantId` |
| `AZURE_SUBSCRIPTION_ID` | `subscriptionId` |
| `ACR_NAME` | `registryName` |

GitHub authenticates using OIDC. The release identity has Contributor on this
resource group and AcrPush on its registry. It cannot grant Azure roles or
modify the Entra application. Runtime image pulls use a separate identity.

The foundation template's `githubOidcSubject` matches this fork's exact subject,
including immutable owner/repository IDs. New GitHub repositories use the
[immutable subject format](https://docs.github.com/en/actions/reference/security/oidc#immutable-subject-claims).
When adapting this deployment to another repository, set that parameter to its
actual environment subject rather than assuming the older name-only format.

The new workflow tests the fork's checked-out commit. Some inherited upstream
workflows explicitly check out upstream; they are not release validation for
this deployment.

To release, run `gh workflow run azure-comfyui.yml --ref master`. Inspect the
workflow result before submitting new jobs. Its log records the previous image
digest and storage snapshot for recovery.

## Validation and recovery

Local or CI container test:

```sh
docker build -f deploy/azure/Dockerfile -t comfyui:test .
bash deploy/azure/test-container.sh comfyui:test
```

The test performs upload → Load Image → Scale Image → Save Image → download,
checks the WebSocket, saves a workflow/settings marker, replaces the container,
and verifies the files survived. It never calls a paid model. On first Azure
deployment, run `python deploy/azure/smoke_test.py` via `az containerapp exec` to
seed the persistent verification fixture. Keep the `azure-smoke` output and
`azure-smoke-test` workflow until the release verification is replaced.

Inspect logs with `az containerapp logs show -g rg-comfyui-dev -n comfyui-dev
--follow`. Restart an idle revision with `az containerapp revision restart`.
Use `az containerapp update -g rg-comfyui-dev -n comfyui-dev --image <old-digest>`
to roll back application code; this does not revert persistent files. Recheck
sign-in, WebSocket connectivity, and `--verify-persistence` afterward.

Snapshots preserve files at release time; they are not an automatic daily backup.
Restore files from the recorded Azure Files snapshot into a recovery directory,
then replace affected files during an idle maintenance window. Share soft delete
retains a deleted share for seven days. Deleting the storage account/resource
group destroys this recovery boundary. Export important media separately.

Rotate the Entra credential before the expiry recorded in `.state.json`: add a
new credential, update the `entra-client-secret` Container Apps secret, validate
fresh sign-in, then remove the old Entra credential. Never print the credential
or paste it into GitHub. Rotate the Azure Files key through the environment's
storage configuration and validate the mount before removing the previous key.

Budget approximately $47–158/month for compute at current East US 2 retail rates,
plus ACR, file capacity/transactions, logs, transfer, and future model usage.
There is no automatic scale-to-zero. Review costs at resource-group scope.

## Foundry video workflow

Open **Workflows → foundry-kling-first-video**. The two nodes are **Kling Text to
Video (Azure Foundry) → Save Video**. No local model downloads or Comfy partner
credits are needed. The default five-second silent Kling clip is approximately
$0.616 at the published rate; actual charges use the Azure offer's pricing.

Set the prompt, duration, aspect ratio, audio option, and take number, then Run.
Identical inputs reuse or resume the existing take, including after restarts.
Changing the prompt/settings or take number submits a new paid job. To get a new
variation of the same shot, increment **take**. Cancelling ComfyUI or reaching the
20-minute polling limit does not cancel the remote job; Run the same inputs to
resume it. An uncertain submission is blocked rather than automatically retried.

The native Save Video node writes playable MP4s under `/data/output/foundry`.
Private job journals, request IDs, original video, and potentially signed result
URLs live under `/data/user/__foundry/jobs`. These system-user files are not
served by the user-data API. Keep them for recovery and duplicate-charge
prevention; they are included in Azure Files snapshots. Do not publish them.

The server uses `DefaultAzureCredential` with `AZURE_CLIENT_ID`,
`FOUNDRY_VIDEO_ENDPOINT`, and `FOUNDRY_VIDEO_DEPLOYMENT`. Browser Entra sign-in is
separate from this runtime identity. The Docker defaults point at the existing
`megastocks-video-7bee` resource and `Fal-Kling-V3-Pro` deployment. Azure tokens
are sent only to that Foundry host, never to cross-origin video CDNs.

An operator with role-assignment permissions provisions access once:

```sh
az deployment group create -g rg-megastocks-illustrations -n comfyui-foundry-access \
  --template-file deploy/azure/foundry.bicep \
  --parameters runtimePrincipalId=<runtime-managed-identity-principal-id>
```

The documented Cognitive Services User role is scoped to the video account.
It includes inference and account key-read permissions; the node uses only
Entra tokens and never retrieves account keys. The GitHub release identity
cannot grant this role. No access to the separate image resource is configured.

Each release installs the starter workflow without overwriting existing edits.
CI runs synthetic video/audio fixtures through every model node, text and image inputs, and Save Video
without Azure credentials or paid requests. To submit the real starter through
the local ComfyUI API, run `python deploy/azure/foundry_workflow.py --submit`
inside the container; monitor its returned ComfyUI prompt ID with `--status`.

If a journal has no request ID, inspect `job.json` and `receipt.json` before
creating a new take. Do not remove the journal merely to retry a timed-out POST:
the provider might already have accepted the charge. Ordinary completed or
interrupted jobs with an ID can be resumed by rerunning their original inputs.


### Additional video models

The same studio now includes four more nodes under **Azure Foundry → video**:

| Node | Resolution choices | Duration | Additional controls |
| --- | --- | --- | --- |
| LTX 2.5 Fast Video | 720p, 1080p, 1440p, 2160p | 6–20 seconds in even increments, or auto | FPS, audio, camera motion |
| LTX 2.5 Pro Video | 720p, 1080p | 6, 8, 10 seconds, or auto | FPS, audio, camera motion |
| MiniMax H3 Max Video | 480P, 768P, 1080P | 0.92–15 seconds | Prompt expansion |
| MiniMax H3 Max Turbo Video | 480P, 768P, 1080P | 0.92–15 seconds | Prompt expansion |

LTX Fast durations above 10 seconds require 720p/1080p at 24/25 FPS. MiniMax
includes generated audio without an audio-off setting; its output can exceed the
requested duration by about 0.7 seconds. Use fixed durations when comparing costs.

Open `foundry-{ltx-fast,ltx-pro,minimax-max,minimax-turbo}-text-to-video` under
Workflows. Each has the model node connected to Save Video. Matching
`-image-to-video` workflows include Load Image: upload/select your first frame
before Run. You can connect another Load Image to `end_image` for the last frame.
LTX requires a first frame when supplying a last frame; MiniMax also accepts an
end frame alone. MiniMax takes the output aspect ratio from the connected image.
Images are PNG data URIs sent to Foundry; no public image hosting is required.
These nodes cover text and keyframe animation, not MiniMax reference-to-video.

The four GlobalStandard pay-as-you-go deployments use the existing Foundry
account and runtime role. `foundry.bicep` provisions them at version `2026-11-02`.
Default deployment names match their catalog names. Override these environment
variables only if using differently named deployments:

| Model | Deployment name override |
| --- | --- |
| LTX Fast | `FOUNDRY_LTX_FAST_DEPLOYMENT` |
| LTX Pro | `FOUNDRY_LTX_PRO_DEPLOYMENT` |
| MiniMax Max | `FOUNDRY_MINIMAX_MAX_DEPLOYMENT` |
| MiniMax Turbo | `FOUNDRY_MINIMAX_TURBO_DEPLOYMENT` |

The original Kling node and its saved take IDs are unchanged. The new nodes use
the same managed identity, durable recovery, and credential-safe downloads.
Outputs are saved under `/data/output/foundry` with a model-specific prefix.
To submit a paid CLI test inside the container, use
`python deploy/azure/foundry_workflow.py --submit --model ltx-fast` or add
`--image existing-input.png` to animate a previously uploaded image.

Schema references (checked November 27, 2026):
[Microsoft Foundry Fal models](https://learn.microsoft.com/en-us/azure/foundry/how-to/fal-ai-models/enable-fal-models),
[LTX Fast](https://fal.ai/models/lightricks/ltx-2.5/text-to-video/fast/api),
[LTX Pro](https://fal.ai/models/lightricks/ltx-2.5/text-to-video/pro/api),
[MiniMax Max](https://fal.ai/models/minimax/h3-max/text-to-video/api),
[MiniMax Turbo](https://fal.ai/models/minimax/h3-max-turbo/text-to-video/api).
