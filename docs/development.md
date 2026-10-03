# Development

## Setup

```bash
make dev                 # .venv with the package (editable) + test/lint tools + service deps
make test lint           # tests + ruff
make up smoke            # Docker stack + end-to-end checks
```

## Repository layout

```
src/ediparse/
  dialect.py       standard + delimiter detection (ISA/UNA/UNB/STX/MSH)
  tokenizer.py     incremental tokenizer: text chunks → Segment/Issue
  envelope.py      interchange/group/message state machine, control checks, events
  model.py         Segment, Message, Group, Interchange, Document, Issue, events
  stream.py        StreamParser (push), stream(), astream(), chunk sources, decoding
  parser.py        whole-document helpers: parse_file/bytes/text
  output.py        events → JSON dicts, CSV rows, summaries, tree text
  textutil.py      helpers for bytes that aren't valid UTF-8 (surrogateescape)
  cli.py           ediparse command
  service.py       FastAPI app (optional [service] extra)
tests/             pytest suite + fuzzing.py (shared mutation fuzzer)
samples/           synthetic EDI files (X12 4010/5010, EDIFACT, TRADACOMS, HL7); used by tests
docs/              documentation + schemas/records.schema.json
reference/         X12 transaction-set registry (JSON)
deploy/            nginx config, Kubernetes base + overlays (local, production)
examples/          stdlib clients (streaming, parallel) and a library example
scripts/           smoke test, fuzzers
tools/             make_samples.py (regenerates X12 samples)
```

## Principles

1. **The core stays dependency-free.** Anything needing third-party packages goes behind an optional extra, like `[service]`.
2. **Never change tokenizer state before the commit point.** `Tokenizer._next()` computes into locals and returns `None` to wait. This is what makes streaming correct for any chunking. The fuzzer enforces it.
3. **Report, don't raise.** New problems become `Issue`s with a stable `code`, attached to the innermost open scope. Raise only for "no usable EDI at all".
4. **Keep `raw`.** Anything the tokenizer skips must be appended to a segment's `raw` or the prefix, so round-trips stay exact.
5. **User-visible text goes through `display()`**, so invalid bytes never reach JSON as lone surrogates.
6. **Linear time, bounded memory.** Never rescan input already examined (resume searches), never build strings by
   repeated `+=` on large or unbounded data (collect in lists, join once), and put a limit on anything that grows
   with input. `test_limits.py` holds the adversarial cases; add one for any new loop over input.
7. **Every output field is in the JSON Schema.** `test_schema.py` enforces it.

## Adding a standard

1. **Detection:** extend `HEADER_RE` and `header_dialect()` in `dialect.py` to recognize the header and build a `Dialect`.
2. **Envelopes:** add a row to `SPECS` in `envelope.py` (interchange / group / message header and trailer tags), plus control checks in `_check_*` if the standard has counts.
3. **Identity:** teach `Message.type/control/version`, `Group.*` and `Interchange.sender/receiver/control/date` in `model.py` where its fields live.
4. **Splitting quirks:** if the tag is separated differently (like TRADACOMS `=`), or some elements are delimiters themselves (like HL7 `MSH-1/2`), handle it in `tokenizer._split()` and `Segment._atomic()`.
5. **Samples and tests:** add synthetic files under `samples/<standard>/`. The sample, streaming, schema and fuzz tests pick them up automatically. Add detection tests in `test_dialect.py`.
6. **Schema and docs:** add the standard to the `standard` enums in `docs/schemas/records.schema.json`, and update the tables in `edi-primer.md` and `architecture.md`.

## Adding an issue code

Add it where it's detected, using `Issue(severity, code, message, segment_index)`. Then add the code to the enum in
the JSON Schema and to the table in [output-format.md](output-format.md#issue-codes).

## Releasing

1. Update `CHANGELOG.md` and the version in `pyproject.toml` and `src/ediparse/__init__.py`.
2. `make test lint`, `make up smoke`, `make k8s-validate`.
3. Tag: `git tag v0.x.y && git push --tags`. `.github/workflows/release.yml` builds a multi-arch image and pushes `ghcr.io/vis-sub/universal-edi-file-parser:v0.x.y` (and `:latest` from `main`).
4. Bump `newTag` in `deploy/kubernetes/overlays/production/kustomization.yaml`.

## Regenerating samples

```bash
make samples        # rewrites samples/x12/** from tools/make_samples.py (envelopes and counts computed)
```
