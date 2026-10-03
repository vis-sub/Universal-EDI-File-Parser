# Deploying and scaling

**Kubernetes is optional.** The service is one stateless container: it keeps no sessions or shared storage, and
nothing it writes outlives a request. Anything that can run several copies of a container behind a load balancer
works. This page covers how scaling works, the options from a single host to managed cloud, and how clients should
send files in parallel.

## How scaling works

- **The unit of work is one file per request.** A request is handled by one parser process from start to finish.
- **Throughput scales with total parser processes** = instances × `WEB_CONCURRENCY`. Aim for about one process per CPU core.
- **Memory per process** is about 60 MB baseline, plus up to `EDIPARSE_SPOOL_MEMORY_MB` (16 MB) per in-flight upload, plus the largest single document being parsed.
- **Disk:** uploads larger than the spool limit spill to `/tmp`. Size `/tmp` to *concurrent requests × largest expected upload*; when it fills, uploads get `507`. If `/tmp` is a tmpfs, those bytes count as container memory ([sizing](operations.md#sizing)).
- **Load balancing** needs nothing special:
  - Round-robin, no sticky sessions.
  - Health check on `GET /healthz`.
  - Turn **response buffering off** so NDJSON streams through.
  - Allow request bodies up to `EDIPARSE_MAX_UPLOAD_MB`.
  - Set idle or read timeouts of several minutes for large files.

A single very large file doesn't get faster with more instances. To parallelize one, split it at interchange
boundaries (`ISA`…`IEA`) on the client and send the pieces concurrently.

## Choosing a platform

| Option | Good for | Scaling | Included here |
|---|---|---|---|
| **Docker Compose + nginx** | One server, dev/test, small teams | `--scale ediparse=N` on one host | `docker-compose.yml`, `deploy/nginx/` |
| **Kubernetes** (EKS, GKE, AKS, on-prem) | Teams already running Kubernetes | HPA autoscaling on CPU across nodes | `deploy/kubernetes/` (base + overlays) |
| **AWS ECS on Fargate** + ALB | AWS without Kubernetes | ECS service auto scaling | recipe below |
| **Google Cloud Run** | Zero-ops, scale to zero, bursty traffic | Automatic, per request | recipe below (mind the size limit) |
| **Azure Container Apps** | Azure without Kubernetes | Built-in HTTP/CPU scaling rules | same container; see notes |
| **VMs** (EC2, Compute Engine, on-prem) + load balancer | Existing VM estates | Add VMs to the LB pool | `docker run` or `pip install` + systemd |
| **No service: library in workers or functions** | Files land in object storage or on a queue | One worker or function per file | recipe below |

Rough guide:
- **One server:** Compose.
- **Already on Kubernetes:** the manifests.
- **On AWS without Kubernetes:** ECS Fargate.
- **On GCP with mostly small files:** Cloud Run.
- **Files arrive in S3/GCS or on a queue and nobody needs to call an API:** skip HTTP and use the library in workers.

All the container options use the published image `ghcr.io/vis-sub/universal-edi-file-parser`, built by
`.github/workflows/release.yml`, or an image you build from the `Dockerfile`.

> **Published image availability.** `:latest` is built on every push to `main`. Version tags such as `:v0.3.0` exist
> only after that tag is pushed to GitHub. New GHCR packages may be private: make it public under the repository's
> **Packages → Package settings** so clusters can pull it without credentials. Until then, build locally (`make
> k8s-local`, or `docker build`) and push to your own registry.

---

### Docker Compose (single host)

```bash
make up                                      # = docker compose up --build -d, then waits for health
make scale N=4                               # = docker compose up -d --scale ediparse=4
make logs                                    # see requests spread across instances
```

nginx re-resolves the instances through Docker DNS, so scaling up or down needs no restart. If you override the host
port with `EDIPARSE_HOST_PORT` and run `docker compose` directly, export it in your shell (`make scale` already passes
`PORT` and uses `--no-recreate`). A later raw `docker compose up --scale` recreates the load balancer and would
otherwise fall back to 8080.

### Kubernetes

```bash
kubectl create namespace edi
kubectl apply -k deploy/kubernetes/overlays/production -n edi
kubectl -n edi port-forward svc/ediparse 8080:80     # try it locally
```

Layout:

```
deploy/kubernetes/
  base/                 Deployment, Service, HPA, PDB (+ ingress.example.yaml)
  overlays/production/  published image at a pinned tag
  overlays/local/       locally built image (imagePullPolicy: Never), smaller CPU/memory requests
```

**Local cluster** (kind, k3s, k3d, Docker Desktop, minikube): build the image, make it visible to the cluster, then
apply the local overlay. `make k8s-local` does the build and apply for the current `kubectl` context.

```bash
docker build -t universal-edi-file-parser:local .
kind load docker-image universal-edi-file-parser:local      # or: k3d image import / minikube image load (Docker Desktop: skip)
kubectl apply -k deploy/kubernetes/overlays/local -n edi
```

These manifests were tested on k3s v1.34:
- pods run under the full security context
- a rolling restart during six in-flight 47 MB streams completed all six
- under load, the HPA scaled 2 → 6 → 10 pods with no failed requests

What's included in `base/`:

| File | What it does |
|---|---|
| `deployment.yaml` | 2 replicas, `WEB_CONCURRENCY=2` with a 2-CPU limit, readiness/liveness on `/healthz` |
| | `enableServiceLinks: false`, so Kubernetes doesn't inject `EDIPARSE_PORT=tcp://…`-style variables |
| | Non-root user, read-only root filesystem, all capabilities dropped |
| | `/tmp` as a 4 Gi `emptyDir` for upload spooling |
| | 300 s termination grace period plus a `preStop` delay, so in-flight streams finish during rollouts and scale-in |
| `service.yaml` | ClusterIP on port 80 |
| `hpa.yaml` | 2–10 replicas at 70% CPU (needs metrics-server), with a 5-minute scale-down window |
| `pdb.yaml` | Keeps at least one pod during node drains |
| `ingress.example.yaml` | ingress-nginx with buffering off, a 1 GB body limit and 600 s timeouts. Add authentication and TLS before exposing it |

Pin the release tag in `overlays/production/kustomization.yaml` (`newTag`). If you raise `WEB_CONCURRENCY`, raise
the CPU limit to match.

### AWS ECS on Fargate

1. Create a task definition with the image:
   - Port 8080
   - 2 vCPU / 2 GB to match `WEB_CONCURRENCY=2`
   - Health check: ECS ignores the image's `HEALTHCHECK`. Either rely on the ALB target health check on `/healthz`, or
     add a task-definition `healthCheck` with the same command:
     `["CMD", "python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8080/healthz', timeout=2)"]`
     (the image has no curl)
   - Ephemeral storage sized for spooled uploads
2. Put an **Application Load Balancer** in front. Target group on HTTP 8080, health path `/healthz`.
3. **Raise the ALB idle timeout** from the default 60 s to around 600 s for large files.
4. Add service auto scaling with target tracking on CPU around 70%.

### Google Cloud Run

Cloud Run pulls from Artifact Registry (or Docker Hub), not from ghcr.io. Push the image to Artifact Registry first,
or create an Artifact Registry *remote repository* that proxies ghcr.io.

```bash
docker build -t REGION-docker.pkg.dev/PROJECT/REPO/universal-edi-file-parser:v0.3.0 . && \
  docker push REGION-docker.pkg.dev/PROJECT/REPO/universal-edi-file-parser:v0.3.0
gcloud run deploy ediparse --image REGION-docker.pkg.dev/PROJECT/REPO/universal-edi-file-parser:v0.3.0 \
  --port 8080 --cpu 2 --memory 2Gi --concurrency 4 --timeout 3600 \
  --set-env-vars WEB_CONCURRENCY=2 --no-allow-unauthenticated
```

Cloud Run limits **HTTP/1 request bodies to 32 MiB**:
- gzip the upload (`Content-Encoding: gzip`). The limit applies to compressed bytes, and EDI usually compresses 10–20×.
- Or use another option for larger files. Cloud Run's HTTP/2 path removes the limit, but needs an h2c-capable server, which uvicorn is not.
- Cloud Run's `/tmp` is in memory, so spooled uploads count against `--memory`.

### Azure Container Apps

Deploy the same image with ingress on target port 8080, set the environment variables above, and add an HTTP or CPU
scale rule. Check the ingress request-size and timeout limits for your plan against your largest files.

### VMs

On each VM, run `docker run -d -p 8080:8080 -e WEB_CONCURRENCY=$(nproc) ghcr.io/vis-sub/universal-edi-file-parser`. Or
use `pip install ".[service]"` with a systemd unit running `ediparse serve --host 0.0.0.0`. Put the VMs behind your
load balancer with the settings from "How scaling works".

### Without a service: library in workers or functions

If files arrive in object storage or on a queue, you may not need an HTTP service at all. Run the library inside
whatever already processes those events. It streams, so memory doesn't grow with file size, even in small functions.

```python
# e.g. an S3-triggered AWS Lambda, or a worker consuming file references from SQS/Kafka/PubSub
import json, boto3
from ediparse import stream, event_to_dict

s3 = boto3.client("s3")

def handler(event, context):
    rec = event["Records"][0]["s3"]
    body = s3.get_object(Bucket=rec["bucket"]["name"], Key=rec["object"]["key"])["Body"]  # streaming body
    for ev in stream(body):                                  # reads in chunks, never the whole object
        if ev.kind == "message":
            publish(json.dumps(event_to_dict(ev)))           # your queue/topic/database
```

Don't put the HTTP service behind a function-as-a-service front door (Lambda function URLs, API Gateway). Their
request payload limits are a few MB (Lambda: 6 MB synchronous; API Gateway REST: 10 MB), far below typical EDI
batches. Function time limits apply too (Lambda: 15 minutes).

---

## Sending files in parallel (clients)

Clients get the parallelism by sending several files at once:

```bash
python examples/parallel_client.py inbound/*.edi --url http://ediparse.internal --workers 8 --out results/
```

[`examples/parallel_client.py`](../examples/parallel_client.py) uses only the standard library. It:
- streams each upload from disk
- writes per-file NDJSON results
- retries connection errors and 5xx responses with backoff
- fails 4xx responses (not EDI, too large) immediately without retrying
- treats a response with no final `summary` record as incomplete

Set `--workers` to about the total number of parser processes behind the load balancer.

**Many tiny files** (thousands of 1–5 KB files): per-request overhead dominates. Concatenating files into one
request works today, because the parser handles many interchanges per body. The trade-off is that records no longer
say which file they came from.

## Security checklist

- No authentication is built in. Run it on a private network, or behind an API gateway or ingress that enforces authentication and TLS.
- Request bodies are never logged. Uploads live only in the per-request spool and are deleted when the response ends.
- For PHI (837/835/834/270/271), keep `/tmp` on tmpfs or encrypted storage and follow your HIPAA controls for the network path.
- The container runs as UID 10001 and works with a read-only root filesystem and all Linux capabilities dropped.
