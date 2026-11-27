# ComfyUI on Azure

One shared, CPU-only studio in `rg-comfyui-dev`, East US 2. All users authenticated
through the configured Entra tenant can access the same queue, settings,
workflows, uploads, and outputs. Tenant guests are included. This is not a
per-user isolation boundary.

## Architecture

Container Apps provides HTTPS, WebSockets, and Entra Easy Auth. ComfyUI listens
on port 8188 with one 2-vCPU/4-GiB replica. The runtime identity pulls from ACR;
it has no Foundry inference permissions yet. Azure Files is mounted at `/data`:

- `/data/input`: uploaded source media.
- `/data/output`: saved results.
- `/data/user`: workflows and settings.

Temporary processing files are ephemeral. The in-memory queue and history do
not survive a restart. The optional SQLite asset catalog is disabled because
this deployment uses a network file share. Media generation and normal workflow
saving do not require that catalog.

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

## Foundry next

Add the image and video nodes in a separate change. Grant the runtime identity
the appropriate data-plane role on each existing Foundry resource, validate the
Fal action explicitly, and use `DefaultAzureCredential` with `AZURE_CLIENT_ID`.
Browser sign-in and server-to-Foundry identity are separate. Persist provider job
IDs before polling; authenticate same-origin protected video downloads without
forwarding credentials to external CDNs. Do not assume ComfyUI's generic partner
nodes use this Azure subscription.
