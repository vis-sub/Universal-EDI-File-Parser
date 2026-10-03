# Security

## Threat model

The service accepts **untrusted files** and returns their contents as JSON. EDI often carries sensitive data:
pricing, bank details (820), and protected health information (837/835/834/270/271).

| Threat | Mitigation |
|---|---|
| Unauthenticated access to parsing (and to data in transit) | **Not built in.** Run on a private network, or behind an API gateway or ingress that enforces authentication and TLS |
| Malformed or hostile input crashing the parser | Fuzz-tested: 200,000+ mutated inputs in-process with invariants checked, and 1,500 over HTTP. Inputs are only ever tokenized as text, never executed or deserialized |
| Memory exhaustion from huge uploads | Streaming parser (flat memory). Upload size cap (`EDIPARSE_MAX_UPLOAD_MB`). The spool spills to disk |
| Disk exhaustion | Spool deleted when each response ends, **including on client disconnect** (a leak here was found and fixed). Size `/tmp` and set `emptyDir.sizeLimit` |
| Compression bombs (`Content-Encoding: gzip`) | Decompression streams in chunks, so memory stays flat. CPU time and output size grow with decompressed size. Cap upstream if needed |
| Data at rest | No database or persistent storage. Request bodies are never logged. Uploads live only in the per-request spool |
| Container escape / privilege | Non-root UID 10001, read-only root filesystem, all Linux capabilities dropped, `allowPrivilegeEscalation: false`, seccomp `RuntimeDefault` (Kubernetes manifests) |
| Vulnerable dependencies | Core library has **zero** dependencies. Service dependencies are pinned (`constraints-service.txt`) and audited. The image is scanned in CI |

## Hardening checklist

- [ ] Put authentication and TLS in front (API gateway, ingress with OAuth/mTLS, or a private VPC only).
- [ ] Keep the service off the public internet unless it's behind that gateway.
- [ ] Set `EDIPARSE_MAX_UPLOAD_MB` and the proxy body limit to your real maximum file size.
- [ ] For PHI: keep `/tmp` on tmpfs or encrypted volumes, and include the network path in your HIPAA controls (TLS everywhere, access logging at the gateway).
- [ ] Pin a release image tag in production (not `:latest`).
- [ ] Watch the CI Trivy gate and rebuild when base-image fixes land.

## Scan results (at time of writing)

| Scan | Result |
|---|---|
| `pip-audit` on pinned service dependencies | No known vulnerabilities |
| Trivy, image, HIGH/CRITICAL | 0 CRITICAL. 44 HIGH, all in Debian base-OS packages (`util-linux`, `ncurses`, `perl-base`…) with **no fix released yet**. None are in Python or the service's dependencies. They affect tools the service never runs. Exploiting them would need code execution inside a non-root, capability-free container first |
| hadolint (Dockerfile), shellcheck, actionlint | Clean |

The image runs `apt-get upgrade` at build time to pick up fixes as Debian releases them. CI fails on any **fixable**
HIGH or CRITICAL vulnerability (`trivy --ignore-unfixed --exit-code 1`).

## Reporting a vulnerability

See [SECURITY.md](../SECURITY.md).
