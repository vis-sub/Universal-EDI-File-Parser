# Testing

## Quick reference

```bash
make test          # 373 unit/integration tests, ~12 s
make lint          # ruff
make fuzz          # 200,000 mutated inputs against the parser's invariants, ~4 min
make up && make smoke                                    # end-to-end against the Docker stack
python scripts/fuzz_service.py http://localhost:8080 1500   # fuzz the running HTTP service
make k8s-validate  # Kubernetes manifests against API schemas
```

CI (`.github/workflows/ci.yml`) runs:
- lint (`ruff`) and tests on Python 3.10–3.13
- an image build, smoke test, and Trivy vulnerability gate
- Compose and Kubernetes manifest validation

## What the test suite covers

| File | Covers |
|---|---|
| `test_samples.py` | Every sample: byte-exact round trip, no errors, every segment placed exactly once in the tree, correct types and versions |
| `test_dialect.py` | Delimiter detection for every standard. 4010 vs 5010 repetition rules. Arbitrary and control-character delimiters. Non-fixed-width ISA |
| `test_edge_cases.py` | Wrapped lines, missing terminators, mixed interchanges/standards, junk/BOM, Latin-1, TA1, control mismatches, missing trailers, stray segments, escapes, empty segments, damaged-header recovery, fuzz regressions |
| `test_stream.py` | Identical results for chunk sizes 1, 2, 3, 7, 64, 1000; byte-at-a-time round trips; early emission; UTF-8 split across chunks; sources (file, iterable, str); `astream`; **flat memory** (5 MB stream with peak traced allocations < 1.5 MB) |
| `test_limits.py` | Resource limits and linear time on adversarial input (no terminator, millions of empty segments, damaged regions, long HL7, overlong ISA), per-message/issue caps, header false positives, HL7 encoding defaults and MLLP framing, UNA repetition rules, explicit codecs |
| `test_fuzz.py` + `fuzzing.py` | Mutation fuzzing with invariants (below), fixed seeds |
| `test_schema.py` | Every record from samples, fuzzed input and the CLI validates against the [JSON Schema](schemas/records.schema.json) |
| `test_service.py` | All endpoints and options, gzip, rejections (400/413/415/422), spill to disk, per-document errors, damaged headers, **client disconnect releases the spool** (real uvicorn server, raw socket) |
| `test_cli.py` | Every command and format, exit codes, bad-port handling |
| `test_docs.py` | Docs don't rot: every relative link and anchor resolves, every `make` target and CLI flag mentioned exists, the getting-started snippet runs |

## Fuzzing

`tests/fuzzing.py` mutates the sample files: byte flips, inserted delimiters and header fragments, deletions,
truncation, duplication, splices from other samples, segment shuffles and random junk. For **every** mutated input
it checks:

1. **No crash:** the only allowed exception is `EDIDetectionError` ("no usable EDI"), and whole-file and streaming parsing must agree on rejection.
2. **Lossless:** `parse_bytes(data).to_bytes() == data`.
3. **Complete:** every segment appears exactly once in the tree.
4. **Chunking-independent:** streaming with a random chunk size gives exactly the same records as one chunk.
5. **Serializable:** every record encodes as UTF-8 JSON.
6. **Retained chunked round trip:** feeding 3 bytes at a time with `retain=True` still reproduces the input.

```bash
python scripts/fuzz.py 10000 20      # cases per seed, number of seeds
```

`scripts/fuzz_service.py` fuzzes a running service over HTTP. Every response must be a clean 4xx, or a 200 that
ends with a `summary` record and contains no `error` record.

## System tests performed

These were run against the real Docker and Kubernetes deployments. They're reproducible with the commands shown.

| Test | How | Result |
|---|---|---|
| Python version matrix | Clean `python:3.10/3.11/3.12/3.13-slim` containers | All tests pass on all four |
| Smoke test | `scripts/smoke-test.sh` against Compose, a single container, and Kubernetes | 11/11 on each |
| Load balancing | `docker compose --scale ediparse=4`, 40 parallel requests | Spread across all 4 instances, 40/40 OK. Scale-down to 2 dropped nothing |
| Large file | 47 MB / 150,000 claims through nginx | Complete, streaming, about 9–11 s metadata-only |
| Concurrent load | 8 × 47 MB at once | All complete and correct in 32.5 s |
| HTTP fuzzing | 1,500 mutated uploads, 16 concurrent | 0 failures, 0 server exceptions |
| Client disconnects | 18 aborted 47 MB streams | `/tmp` back to 0 KB, no exceptions |
| Graceful shutdown | SIGTERM 2 s into a 10 s stream | Request completed with all 150,000 documents |
| Hardened runtime | `--read-only`, `--cap-drop ALL`, `no-new-privileges`, 47 MB upload spilling to `/tmp` | Works |
| Kubernetes deploy | k3s v1.34 in Docker, `overlays/local` | Pods healthy under the full security context |
| Rolling restart under load | `kubectl rollout restart` during 6 in-flight 47 MB streams | 6/6 complete with summary |
| Autoscaling | 150 s of load | HPA scaled 2 → 6 → 10 pods, 66/66 requests OK |
| Manifests | kubeconform (strict) on both overlays + ingress | 9/9 valid |
| Static analysis | ruff, hadolint, shellcheck, actionlint | Clean |
| Security | pip-audit, Trivy | See [Security](security.md#scan-results-at-time-of-writing) |

## Bugs found by testing

Every one is fixed and has a regression test.

| Found by | Bug | Fix |
|---|---|---|
| Fuzzing | Results for non-UTF-8 input depended on chunk size (the encoding switched mid-stream) | `surrogateescape` decoding; invalid bytes shown as Latin-1 |
| Fuzzing | The tokenizer set its "wrapped lines warned" flag before a segment was complete, so the notice could be lost | All state changes moved to the commit point |
| Fuzzing | The leading-junk count differed by chunking | Count non-blank characters exactly |
| Fuzzing | Crash: `'18²0001'` passes `str.isdigit()` but `int()` rejects it. The service's Content-Length check had the same pattern (a potential 500) | `isascii() and isdigit()` |
| Fuzzing | A damaged interchange header aborted the whole stream | Report `bad_header` and resync at the next header |
| Fuzzing | Error offsets were relative to the tokenizer's buffer, not the file | Absolute offsets added by the tokenizer |
| Fuzzing | Header lookalikes split across chunks (`x\|ISA*`) were taken for headers | Keep one character of lookbehind context at cuts |
| Fuzzing | The invalid-UTF-8 notice was raised from text appended to `raw` later | Check at segment creation |
| HTTP fuzzing | An unreadable first header gave 200 + an in-band error instead of 422 | Tokenizer probe before streaming |
| HTTP fuzzing | Non-UTF-8 bytes in tags/delimiters crashed JSON encoding mid-stream (dropped connections) | Map to Latin-1 for display; `output.dumps()` guarantees encodable JSON; new fuzz invariant |
| Disconnect test | Client hang-ups leaked the spooled upload (`/tmp` space never freed) | Response-level cleanup that always runs |
| Kubernetes | A Service named `ediparse` injected `EDIPARSE_PORT=tcp://…`, crashing pods | Settings renamed to `EDIPARSE_HTTP_*`; `enableServiceLinks: false` |
| Linters | Unclosed file handle in the CLI; non-numeric `USER` in the image; unpinned service dependencies | Fixed; `constraints-service.txt` |
| Independent code review | No segment-length limit: input with no terminator grew memory without bound in quadratic time (64 MB: 10 s, 3 GB); via gzip a ~1 MB request could do it | `max_segment` limit with recovery; resumable terminator search; bounded gzip output with a total cap |
| Independent code review | Quadratic time on runs of empty segments, HL7 terminator search, damaged-region skipping, and long whitespace padding | Regex fast paths, combined `\r\|\n` search, skipped text kept in lists only when retaining |
| Independent code review | CPU-heavy pre-check (gzip + probe) ran on the event loop, stalling the worker | Runs in a worker thread |
| Independent code review | 500 on `/v1/validate`/`/v1/detect` for a bad `encoding`, strict-codec decode errors and gzip corruption | Encoding validated up front (422); codec errors impossible (`surrogateescape`); gzip errors are 400 |
| Independent code review | `Status: UNAVAILABLE` in a banner taken for an EDIFACT `UNA` header, silently losing the file; `Subject: ISA, GS` marked valid files invalid | `UNA` must be followed by a delimiter and its service characters are validated; header-like words before the first readable header are leading data (warning) |
| Independent code review | Multi-member gzip read only partly; truncated gzip accepted silently | All members read; truncation is 400 |
| Independent code review | HL7 `MSH\|^~\|` gave escape = `^`, corrupting values; BHS group type was `\|`; MSH-12 version not split | Correct defaults; `None`; first component |
| Independent code review | Unbounded per-message segments and per-scope issues when streaming | `max_message_segments`, `max_issues` (service defaults 250,000 / 1000) |
| Independent code review | `/tmp` full gave 500; large non-EDI bodies got 200 + in-band error; CLI shorthand and broken-pipe bugs; unvalidated settings | 507; 422; fixed; validated at startup |
| Docs audit | Overstated "flat memory" claims; wrong Cloud Run/ECS steps; several X12 code meanings | Corrected (see CHANGELOG) |

## Adding tests

- **A parser fix:** add a regression test in `test_edge_cases.py` or `test_stream.py`, and if the fuzzer found it, say so in the docstring.
- **A new sample file:** drop it in `samples/<standard>/`. It's picked up automatically by the sample, streaming, schema and fuzz tests. Samples must be synthetic (no real data).
- **A new output field:** update `docs/schemas/records.schema.json`. `test_schema.py` fails until you do.
