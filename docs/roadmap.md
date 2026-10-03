# Roadmap

Ordered roughly by value to consumers. Nothing here is committed to a date.

## Next: meaning, not just structure

| Item | What it adds |
|---|---|
| **Loop resolution** | Group each document's segments into named loops: X12 HL hierarchies (856, 837, 270/271, 276/277), loop-start tables for header/detail/summary documents (850, 810, 855…), LX/S5/W-segment patterns. The seven archetypes in the [document catalog](document-catalog.md) cover all 319 X12 sets |
| **Field labels** | Element and segment names from dictionaries (e.g. `N101 "ST"` → ship-to party; `NM1*85` → billing provider), returned alongside positional IDs |
| **Partner overlays** | Small YAML files describing a trading partner's deviations, without code changes |
| **Heuristic loops** | For documents with no definition: infer loops from repeating segment patterns, clearly flagged as inferred |

## Service

| Item | Why |
|---|---|
| Async jobs API (`POST /v1/jobs` → poll or webhook; results to object storage) | Very large or scheduled batches without holding a connection open |
| Zip/tar batch upload with a per-record `file` field | Many small files in one request |
| Prometheus `/metrics` (requests, bytes, documents, issues by code, durations) | Production monitoring |
| Optional built-in auth (API keys / OIDC) | Teams without a gateway |
| Per-claim splitting of very large single documents (e.g. one 837 ST/SE with thousands of claims) | Removes the "largest document" memory bound |

## Parser

| Item | Why |
|---|---|
| EDIFACT/X12 binary segments (`BIN`, `BDS`) | Skip binary payloads by declared length |
| HL7 subcomponents (`&`) as structured values | Currently left inside the component |
| EBCDIC and charset declared in EDIFACT `UNB01` (UNOA–UNOY) | Decode by the declared syntax ID |
| Implementation-guide validation (HIPAA SNIP 3+) | Beyond envelope integrity |
| Faster JSON output (e.g. `orjson` as an optional extra) | JSON serialization dominates full-output time |

## Done

- Streaming parser for X12, EDIFACT, TRADACOMS and HL7 v2, chunking-independent and lossless
- HTTP service, Docker, Compose with load balancer, Kubernetes base and overlays
- Fuzzing, JSON Schema, CI with version matrix, image scanning and manifest validation
