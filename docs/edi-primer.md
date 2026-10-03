# EDI primer for developers

Enough EDI to use this project confidently. No prior EDI experience assumed.

## The shape of every EDI file

An EDI file is plain text made of **segments**. A segment is a line-like record that starts with a 2–3 character
**tag** and is split into **elements** by a delimiter.

```
BEG*00*SA*PO-10045**20261001~
│   │  │  │        │ └ element 5: order date
│   │  │  │        └ element 4: (empty)
│   │  │  └ element 3: PO number
│   │  └ element 2: "SA" = stand-alone order
│   └ element 1: "00" = original
└ tag: BEG (beginning of purchase order)
                                     ~ = segment terminator
```

Elements can be **composite**, meaning split further into components, and some can **repeat**:

```
SV1*HC:99213*100~          SV101 is composite: HC (code type) : 99213 (procedure)
EB*1*IND*30^1^98~          EB03 repeats (X12 5010): 30, 1 and 98
```

Segments are grouped into nested **envelopes**:

```
ISA ─ Interchange: one transmission from a sender to a receiver
 GS ─ Functional group: documents of one type (e.g. all purchase orders)
  ST ─ Transaction set: ONE business document (e.g. one purchase order)
     … BEG, N1, PO1, … the document's content
  SE ─ end of document   (SE01 = segment count, SE02 = ST02)
 GE ─ end of group       (GE01 = number of documents, GE02 = GS06)
IEA ─ end of interchange (IEA01 = number of groups, IEA02 = ISA13)
```

A file can contain several interchanges. Each interchange can contain several groups, and each group several
documents. The trailers carry **counts and control numbers** that let the receiver check nothing was lost. This
project checks all of them.

## Delimiters are not fixed

Different senders use different delimiters, and **the file tells you which**:

| Standard | Where the delimiters are declared |
|---|---|
| **X12** | The `ISA` segment is fixed-width (106 characters):<br/>• the 4th character is the element separator<br/>• the character after the 16th element separator is the component separator, and the next one is the segment terminator<br/>• in version 00402 and later, `ISA11` is the repetition separator |
| **EDIFACT** | An optional `UNA` segment lists six service characters: component, element, decimal mark, release (escape), repetition, terminator. Without `UNA`, the defaults `:+.? '` apply |
| **TRADACOMS** | Fixed: `=` after the tag, `+` elements, `:` components, `'` terminator, `?` escape |
| **HL7 v2** | `MSH`: the 4th character is the field separator, followed by the encoding characters (`^~\&` = component, repetition, escape, subcomponent) |

This is why the parser needs no configuration. It reads these declarations from every interchange.

## The four standards

| | X12 | EDIFACT | TRADACOMS | HL7 v2 |
|---|---|---|---|---|
| Region / industry | North America; retail, logistics, US healthcare (HIPAA) | International; retail, logistics, customs | UK retail (legacy) | Healthcare clinical messaging |
| Interchange | `ISA`…`IEA` | `UNA`? `UNB`…`UNZ` | `STX`…`END` | `FHS`…`FTS` (optional) |
| Group | `GS`…`GE` | `UNG`…`UNE` (optional) | — | `BHS`…`BTS` (optional) |
| Document | `ST`…`SE` | `UNH`…`UNT` | `MHD`…`MTR` | `MSH`… (no trailer) |
| Document type | `ST01` (850, 810, 837…) | `UNH02` (ORDERS, INVOIC…) | `MHD02` (ORDERS, INVFIL…) | `MSH-9` (ADT^A01…) |
| Escape character | none | `?` | `?` | `\` sequences (`\F\`, `\S\`…) |

## Common X12 documents

| Code | Document | Typical flow |
|---|---|---|
| 850 / 855 / 860 | Purchase order / acknowledgment / change | Buyer → seller → buyer |
| 856 | Ship notice (ASN) | Seller → buyer before delivery |
| 810 | Invoice | Seller → buyer |
| 820 | Payment / remittance | Payer → payee |
| 940 / 945 | Warehouse shipping order / advice | Owner ↔ 3PL |
| 204 / 990 / 214 / 210 | Load tender / response / status / freight invoice | Shipper ↔ carrier |
| 837 / 835 | Healthcare claim / payment (ERA) | Provider → payer → provider |
| 834 | Benefit enrollment | Employer → insurer |
| 270 / 271, 276 / 277 | Eligibility and claim-status inquiry / response | Provider ↔ payer |
| 997 / 999 | Functional / implementation acknowledgment | Receiver → sender |

The [document catalog](document-catalog.md) shows the segment layout of each, and the
[registry](../reference/x12_transaction_sets.json) lists all 319 X12 transaction sets.

## X12 4010 vs 5010

These are versions of the X12 standard. `ISA12` (`00401` or `00501`) says which syntax rules the envelope follows,
and `GS08` gives the exact version of the documents inside (e.g. `005010X222A1` = 5010 837 Professional).

| | 4010 | 5010 |
|---|---|---|
| `ISA11` | Usually `U` (a standards ID) | Repetition separator (e.g. `^`) |
| `ST03` | — | Implementation guide ID (HIPAA) |
| Acknowledgment | 997 | 997 or 999 |
| HIPAA documents | ICD-9 era | Required for ICD-10 |

The parser handles both automatically. Notably, a 4010 file's `U` in ISA11 is never treated as a delimiter, so text
like `ACME UNIVERSAL` isn't split.

## Loops: the part that needs knowledge

Inside a document, segments form **loops**: repeating groups such as "each line item" (`PO1` and the segments after
it) or, in an 837, "billing provider → subscriber → claim → service line". Most loops aren't marked in the data. You
know a `PO1` starts a line-item loop because the 850 spec says so. The exception is X12's `HL` segment, which carries
explicit parent/child IDs.

This project returns each document's segments in order, with positions. Grouping them into named loops needs
per-document knowledge. It's the next layer on the [roadmap](roadmap.md), and the [catalog](document-catalog.md)
describes the patterns.

## Glossary

| Term | Meaning |
|---|---|
| **Segment** | One record: a tag plus elements, ended by a terminator |
| **Element** | One field of a segment, identified by position (`N103` = 3rd element of `N1`) |
| **Composite / component** | An element split into parts by the component separator |
| **Repetition** | An element that occurs several times, split by the repetition separator |
| **Interchange** | One transmission envelope (ISA/IEA, UNB/UNZ, STX/END) |
| **Functional group** | A batch of documents of one type (GS/GE, UNG/UNE) |
| **Transaction set / message** | One business document (ST/SE, UNH/UNT, MHD/MTR, MSH) |
| **Control number** | An ID in a header, repeated in its trailer, used to detect loss or duplication |
| **Release character** | An escape that makes the next character literal (EDIFACT/TRADACOMS `?`) |
| **Implementation guide** | A spec narrowing a standard for one use (e.g. HIPAA 837P `005010X222A1`) |
| **Trading partner** | The company you exchange EDI with |
| **VAN / AS2** | Ways EDI files are transported. Out of scope here |
