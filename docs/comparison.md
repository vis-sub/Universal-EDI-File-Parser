# How it compares

This is the first public iteration (v0.3.0). It focuses on one job, reading any EDI file into clean JSON with zero
configuration, and on doing that robustly at scale. Several mature open-source projects cover other parts of the EDI
problem better today. The [roadmap](roadmap.md) is built around closing those gaps.

Licenses and activity were checked on GitHub and SourceForge in October 2026. In the table, "—" means we didn't find it
as a documented feature. Corrections are welcome.

## Open-source alternatives

| Project | Language | License | Standards | Reads | Writes EDI | Validates (schemas / guides) | Runs as a service |
|---|---|---|---|---|---|---|---|
| **This project** | Python | MIT | X12, EDIFACT, TRADACOMS, HL7 v2 | Streaming, zero-config | — | Envelopes and control totals | Yes (HTTP + Docker + Kubernetes) |
| [StAEDI](https://github.com/xlate/staedi) | Java | Apache-2.0 | X12, EDIFACT | Streaming | Yes | Yes (schemas, customizable) | — |
| [EDI.Net](https://github.com/indice-co/EDI.Net) | C# | MIT | X12, EDIFACT, TRADACOMS | Into your annotated classes | Yes | — | — |
| [EdiWeave](https://github.com/EdiWeave/EdiWeave) | C# | LGPL-3.0 | X12/HIPAA, EDIFACT, EANCOM, VDA, PNRGOV | Yes | Yes | Yes | — |
| [pyx12](https://github.com/azoner/pyx12) | Python | BSD-style | X12 (HIPAA focus) | Yes | — | Yes (HIPAA implementation guides) | — |
| [pydifact](https://github.com/nerdocs/pydifact) | Python | MIT | EDIFACT | Yes | Yes | — | — |
| [EDIReader](https://github.com/BerryWorksSoftware/edireader) | Java | GPL-3.0 | X12, EDIFACT | To XML/JSON | — | — | — |
| [imsweb x12-parser](https://github.com/imsweb/x12-parser) | Java | BSD-style | X12 | Yes | — | — | — |
| [gozer](https://github.com/walmartlabs/gozer) | Java | Apache-2.0 | X12 | Yes | — | — | — |
| [omniparser](https://github.com/jf-tech/omniparser) | Go | MIT | EDI among CSV/JSON/XML | Streaming ETL with transforms | — | — | — |
| [Bots](https://sourceforge.net/projects/bots/) | Python | GPL-3.0 | Many | Full translator (mappings, routing, partners) | Yes | Yes | Yes (its own server) |

Commercial platforms (Stedi, SPS Commerce, Cleo, OpenText and others) offer full managed EDI, including transport,
partner onboarding and mapping. They're outside this comparison.

**License notes:**
- **MIT, Apache-2.0 and BSD** are permissive: you can embed them, including in closed-source products. This project is MIT.
- **LGPL (EdiWeave):** you can link to it from closed-source code, but changes to the library itself must be shared.
- **GPL (EDIReader, Bots):** distributing software that includes them requires releasing it under the GPL.

## Where this project is strongest today

- **Zero configuration across four standards.** Delimiters and envelopes are read from each interchange, so files from any partner work without setup.
- **A language-neutral service.** Any team can POST a file and stream back one JSON object per business document. It comes with Docker, Compose with a load balancer, and Kubernetes manifests tested on a real cluster, including autoscaling and rolling restarts.
- **Robustness on messy and hostile input:**
  - fuzz-tested: 400,000+ mutated inputs, with byte-exact round trips and results that don't depend on chunking
  - per-document error isolation
  - recovery from damaged headers
  - hard resource limits
  - linear time
- **Lossless.** Every input byte is accounted for, and the parsed tree reproduces the file exactly.
- **Documented and typed output:** a JSON Schema for every record, and operational docs (sizing, security, runbooks).

## Where the others are ahead (and the plan)

| Gap | Who does it well today | Planned |
|---|---|---|
| Field meaning: loops and element names instead of `NM103` | pyx12, EdiWeave, StAEDI (schemas) | [Phase 1](roadmap.md#phase-1-meaning) |
| Generating acknowledgments (997, 999, TA1, CONTRL) | EdiWeave, Bots | [Phase 2](roadmap.md#phase-2-respond-and-write) |
| Writing EDI from JSON | StAEDI, EDI.Net, EdiWeave, pydifact, Bots | [Phase 2](roadmap.md#phase-2-respond-and-write) |
| Validation against schemas and implementation guides (HIPAA SNIP 1–7, partner rules) | pyx12, StAEDI, EdiWeave | [Phase 3](roadmap.md#phase-3-validate) |
| Raw throughput (Java/.NET parse several times faster than about 6 MB/s per core) | StAEDI, EDI.Net | [Phase 4](roadmap.md#phase-4-speed-and-reach) |
| More standards and subsets (EANCOM, VDA, ODETTE) | EdiWeave | [Phase 4](roadmap.md#phase-4-speed-and-reach) |
| Mapping and partner management | Bots, commercial platforms | Out of scope for now: this project stays a component that fits into those pipelines |

## Choosing today

| You need | Good choice now |
|---|---|
| EDI data into your systems as JSON, from any language, at scale | **This project** |
| To inspect, validate envelopes, or convert files to NDJSON/CSV | **This project** (CLI) |
| To generate EDI or acknowledgments in Java | StAEDI |
| Typed EDI objects in .NET | EDI.Net, EdiWeave |
| HIPAA implementation-guide validation in Python | pyx12 |
| A complete open-source translator with mappings and partners | Bots |

Several of these combine well. For example, use this service to ingest and route documents, and pyx12 to validate
the HIPAA ones, until Phase 3 lands.
