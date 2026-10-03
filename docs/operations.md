# Operations

Running the service in production: sizing, performance, monitoring, troubleshooting and runbooks.

## Performance (measured)

Measured on an Apple-silicon laptop (11 cores), Python 3.12. The input was a 47 MB X12 file with 150,000 837
claims (1.16 million segments).

| What | Result |
|---|---|
| Library, parse only, one process | 8.5 s (**≈ 5.6 MB/s**), peak RSS **22 MB** |
| Library, parse + JSON metadata (`segments=False`) | 10.1 s (≈ 4.7 MB/s) |
| Library, parse + full JSON (293 MB of output) | 30.6 s (≈ 1.5 MB/s). JSON serialization dominates |
| Service, one request, metadata only | ≈ 10–11 s |
| Service, one request, full output | ≈ 29 s |
| Service, **8 concurrent** 47 MB requests (2 instances × 2 workers) | All finished in **32.5 s** (≈ 11.6 MB/s aggregate). All 8 × 150,000 documents correct |
| Kubernetes (k3s), load across an HPA scale-up from 2 to 10 pods | 66/66 requests HTTP 200 |

Rules of thumb:
- **Throughput scales with parser processes** (instances × `WEB_CONCURRENCY`), about 5 MB/s each for metadata output.
- **`segments=false` is about 3× faster** than full output. Use it for routing, indexing and validation, then fetch full documents only where needed.
- **One request uses one core.** A single huge file doesn't speed up with more instances. Split large files at interchange boundaries on the client to parallelize them.

## Sizing

Per worker process:
- about **60 MB** baseline (Python + FastAPI)
- plus the **largest single document** being parsed (usually KBs, sometimes MBs)

Per in-flight request:
- up to **`EDIPARSE_SPOOL_MEMORY_MB`** (16 MB) of upload held in memory
- the rest of the upload on `/tmp`

| Setting | Guidance |
|---|---|
| CPU | 1 core per worker. Set `WEB_CONCURRENCY` equal to the container's CPU limit |
| Memory limit | `workers × 100 MB` + `concurrent requests × 16 MB` + headroom. The manifests use 1 Gi for 2 workers |
| `/tmp` size | `concurrent requests × typical upload size` |
| `/tmp` on tmpfs? | **Spooled bytes then count as container memory.** Measured: 4 concurrent 47 MB uploads per container showed about 314 MiB. That's spool, not parser memory. Use disk-backed `/tmp` (the Kubernetes `emptyDir` default) for large files, or size the memory limit to include it |

## Configuration reference

| Variable | Default | Effect |
|---|---|---|
| `WEB_CONCURRENCY` | `1` | Worker processes per instance |
| `EDIPARSE_HTTP_HOST` / `EDIPARSE_HTTP_PORT` | `127.0.0.1` / `8080` (image: `0.0.0.0`) | Bind address |
| `EDIPARSE_MAX_UPLOAD_MB` | `1024` | Uploads larger than this get 413 |
| `EDIPARSE_SPOOL_MEMORY_MB` | `16` | Upload bytes kept in memory before spilling to `/tmp` |
| `EDIPARSE_CHUNK_KB` | `64` | Read size for the parser |
| `EDIPARSE_MAX_ISSUES` | `1000` | Cap on issues listed by `/v1/validate` |

The names avoid `EDIPARSE_PORT` and `EDIPARSE_HOST` on purpose. Kubernetes injects `EDIPARSE_PORT=tcp://…` into
pods when a Service is named `ediparse`, which crashed pods in testing. The manifests also set
`enableServiceLinks: false`.

## Health and monitoring

| Signal | Where |
|---|---|
| Liveness/readiness | `GET /healthz` → `{"status":"ok"}`. It responds while workers are busy parsing, because parsing runs in worker threads |
| Configured limits | `GET /v1/info` |
| Access logs | uvicorn, on stdout: method, path, status. Request bodies are never logged |
| Load balancer logs | nginx access log on stdout (Compose) |
| Per-file outcome | The `summary` record (`errors`, `warnings`, `valid`). Clients should log it |

There's no metrics endpoint yet (see the [roadmap](roadmap.md)). Useful alerts today:
- the 5xx rate in load-balancer or access logs (it should be zero: parse problems are 200 + issues, bad input is 4xx)
- p95 request duration growing with file size
- container restarts / OOM kills
- `/tmp` usage
- HPA pinned at `maxReplicas`

## Troubleshooting

| Symptom | Likely cause | Action |
|---|---|---|
| Records arrive all at once at the end | A proxy is buffering the response | nginx: `proxy_buffering off`. ingress-nginx: `proxy-buffering: "off"`. ALB/Cloud Run stream by default |
| `413` | Upload > `EDIPARSE_MAX_UPLOAD_MB`, or the proxy's body limit | Raise both together (nginx `client_max_body_size`, ingress `proxy-body-size`) |
| `422 Not usable EDI` | No readable header in the first 1 MiB | Check the client sends the raw file. Check for a damaged ISA (`ediparse detect file.edi`) |
| Response ends without a `summary` | Client/proxy timeout, or a pod killed mid-stream | Raise proxy read timeouts (600 s+). Check for OOM kills. Retry the file |
| Pods crash at startup with `invalid port 'tcp://…'` | `EDIPARSE_HTTP_PORT` overridden with a Kubernetes service-link value | Use the provided manifests (`enableServiceLinks: false`), or don't name env vars after the Service |
| OOM kills | tmpfs `/tmp` counted against memory, or a single huge document | Use disk-backed `/tmp`; raise the memory limit; split huge documents upstream |
| Throughput lower than expected | Too few workers for the CPU, or full output where metadata would do | Match `WEB_CONCURRENCY` to CPU; use `segments=false`; add replicas |
| `valid: false` on documents | Control counts/numbers wrong, missing trailers | Read `issues`. Often the sender's problem; the data is still returned |
| Values contain odd accented characters | The file isn't UTF-8 | Expected: invalid bytes are shown as Latin-1 (`invalid_utf8` issue). Pass `encoding=cp1252` (or the right codec) if known |

## Runbooks

**Deploy a new version (Kubernetes)**

```bash
# bump newTag in deploy/kubernetes/overlays/production/kustomization.yaml, then
kubectl apply -k deploy/kubernetes/overlays/production -n edi
kubectl -n edi rollout status deploy/ediparse
```

In-flight requests finish: there's a 300 s grace period and a `preStop` delay. Tested with six concurrent 47 MB
streams during a rolling restart: all completed.

**Roll back**

```bash
kubectl -n edi rollout undo deploy/ediparse
```

**Scale manually** (when there's no HPA, or to override it temporarily)

```bash
kubectl -n edi scale deploy/ediparse --replicas=6        # Kubernetes
make scale N=6                                           # Compose
```

**Investigate a problem file**

```bash
ediparse detect problem.edi          # standard, delimiters, interchanges
ediparse validate problem.edi        # every issue, with segment index
ediparse -f tree problem.edi         # structure outline
```

**Refresh pinned dependencies** (monthly, or when a CVE lands)

```bash
docker build -t universal-edi-parser . && \
docker run --rm --entrypoint pip universal-edi-parser freeze --exclude ediparse   # compare / update constraints-service.txt
make test && make up && make smoke
```
