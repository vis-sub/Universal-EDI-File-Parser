# Roadmap

v0.3.0 is the first public iteration. It reads any X12, EDIFACT, TRADACOMS or HL7 v2 file into JSON with zero
configuration, as a library, CLI or scalable service. The goal from here is the best open-source tool for getting
EDI into and out of modern systems. That means matching what the established projects do well (see
[comparison](comparison.md)), while keeping what's distinctive here:
- zero configuration
- language-neutral streaming service
- robustness
- lossless output

Phases are ordered by value to users. Nothing has a committed date, and contributions are welcome
([CONTRIBUTING](../CONTRIBUTING.md)).

## Phase 1: meaning

Today a consumer gets `NM103` and has to know X12 to use it. Phase 1 makes the output self-describing, without
giving up zero-config parsing.

| Item | What it adds | Prior art |
|---|---|---|
| **Loop resolution** | Group each document's segments into named loops: X12 HL hierarchies (856, 837, 270/271, 276/277), loop-start tables for header/detail/summary documents (850, 810, 855…), LX/S5/W-segment patterns. The seven archetypes in the [document catalog](document-catalog.md) cover all 319 X12 sets | pyx12 maps, StAEDI schemas |
| **Field labels** | Segment and element names alongside positional IDs (`NM1*85` → billing provider, `N101 "ST"` → ship-to), from data files | EdiWeave, StAEDI |
| **Top documents first** | Full definitions for 850, 810, 856, 855, 997/999, 837P/I, 835, 834, 270/271, 276/277, then EDIFACT ORDERS, INVOIC, DESADV | — |
| **Heuristic loops** | For documents without a definition: infer loops from repeating segment patterns, flagged as inferred | — |
| **Partner overlays** | Small YAML files describing a trading partner's deviations, without code changes | StAEDI schema customization |
| **Lazy element splitting** | Store raw text and split on access, roughly halving the memory of large held documents | — |

## Phase 2: respond and write

Real trading relationships need replies, and integrations need to send EDI, not just read it.

| Item | What it adds | Prior art |
|---|---|---|
| **Acknowledgment generation** | Produce 997, 999 and TA1 (X12) and CONTRL (EDIFACT) from the parse result, with the correct control numbers and error codes | EdiWeave, Bots |
| **EDI writer** | JSON (the same record format) → EDI, with delimiters, escaping, envelopes and control totals generated. Round-trip tested against the reader | StAEDI, EDI.Net, pydifact |
| **Service endpoints** | `POST /v1/ack` (returns the acknowledgment for an uploaded file), `POST /v1/write` | — |

## Phase 3: validate

| Item | What it adds | Prior art |
|---|---|---|
| **Schema validation** | Required segments, element lengths and types, code lists, loop repeats, from the Phase 1 definitions | StAEDI, EdiWeave |
| **HIPAA SNIP levels** | Levels 1–7 for 837/835/834/270/271/276/277 | pyx12 |
| **Partner rules** | Companion-guide constraints in the overlays | — |

## Phase 4: speed and reach

| Item | Why | Prior art |
|---|---|---|
| **Faster core** | An optional compiled tokenizer (Rust/C extension) behind the same API; Java/.NET libraries parse several times faster than about 6 MB/s per core | StAEDI, EDI.Net |
| **Faster JSON** | Optional `orjson`; JSON output dominates full-output time | — |
| **More standards and subsets** | EANCOM (EDIFACT subset), VDA, ODETTE; EDIFACT/X12 binary segments (`BIN`, `BDS`); charset from EDIFACT `UNB01` (UNOA–UNOY), EBCDIC | EdiWeave |
| **HL7 details** | Subcomponents (`&`) as structured values | — |

## Service and ecosystem

| Item | Why |
|---|---|
| Async jobs API (`POST /v1/jobs` → poll or webhook; results to object storage) | Very large or scheduled batches without holding a connection open |
| Zip/tar batch upload with a per-record `file` field | Many small files in one request |
| Prometheus `/metrics` | Production monitoring |
| Optional built-in auth (API keys / OIDC) | Teams without a gateway |
| Per-claim splitting of very large single documents | Removes the "largest document" memory bound |
| PyPI package and generated client SDKs (from the OpenAPI spec and JSON Schema) | Easier adoption |

## Out of scope (for now)

Transport (AS2, SFTP, VAN), partner onboarding and visual mapping are what full translators and commercial
platforms provide. This project aims to be the best component inside those pipelines, not a replacement for them.

## Done in v0.3.0

- Streaming parser for X12, EDIFACT, TRADACOMS and HL7 v2: zero-config, chunking-independent, lossless, linear time, with hard resource limits
- HTTP service (NDJSON per document, gzip, validation, detection), Docker, Compose with load balancer, Kubernetes base and overlays
- Fuzzing (400,000+ cases), JSON Schema, independent code review and docs audit, CI with version matrix, image scanning and manifest validation
