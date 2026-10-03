# X12 Document Catalog (004010 & 005010)

What every X12 document looks like, from the parser's point of view.

- **Full list:** [`reference/x12_transaction_sets.json`](../reference/x12_transaction_sets.json) covers all **319** transaction sets
  (293 in 004010, 318 in 005010), with subcommittee, version availability, GS01 functional group,
  HIPAA implementation guides, EDIFACT equivalent, and a usage tier.
- **Example files:** [`samples/x12/`](../samples/x12/) has synthetic files for the common documents in both versions.
  Regenerate them with `python3 tools/make_samples.py`.

Notation used below: `[ ]` means optional, `{ }` means a repeating loop, and indentation shows nesting.

---

## 1. Every file has the same outer structure

```
ISA  Interchange header     fixed 106 chars; defines delimiters; ISA12 = 00401 | 00501
 GS  Functional group       GS01 = group type (PO, IN, SH, HC…); GS08 = version (004010, 005010X222A1…)
  ST Transaction set        ST01 = document type (850, 837…); ST02 = control #; ST03 = guide (5010 only)
     …body…
  SE Transaction trailer    SE01 = segment count incl. ST/SE; SE02 = ST02
 GE  Group trailer          GE01 = number of ST/SE sets; GE02 = GS06
IEA  Interchange trailer    IEA01 = number of GS/GE groups; IEA02 = ISA13
```

One file can contain several ISA envelopes, each ISA several GS groups, and each GS several ST
documents. All documents in a GS group are the same functional type (GS01).

**4010 vs 5010 at the envelope level**

| | 004010 | 005010 |
|---|---|---|
| ISA11 | `U` (standards ID) | repetition separator (often `^`) |
| ISA12 | `00401` | `00501` |
| ST03 | does not exist | implementation guide ID (required in HIPAA, e.g. `005010X222A1`) |
| Acknowledgment | 997 | 997, or 999 (required for HIPAA) |
| TA1 (interchange ack) | yes | yes |

---

## 2. Seven structural archetypes

The body of each of the 319 documents follows one, or a mix, of these seven patterns. The parser needs
a loop resolver for each pattern, not one per document. Detailed specs per document are optional add-ons.

| # | Archetype | How loops are formed | Examples |
|---|---|---|---|
| A | **Header / Detail / Summary** | A known "loop start" segment begins each loop (N1, PO1, IT1, LIN) and the loop ends when that segment repeats or a summary segment (CTT, TDS) appears | 850, 855, 860, 865, 810, 880, 875, 832, 846, 852, 830, 862 |
| B | **HL hierarchy** | `HL*id*parent*level` gives parent/child links **in the data itself**, so the tree can be built with no schema | 856, 837, 270/271, 276/277, 278, 857 |
| C | **LX-numbered detail** | `LX*n` starts each detail group | 214, 210, 940, 945, 835 (claim groups), 820 (HIPAA) |
| D | **Stop / event loops** | One segment per stop or event (S5, AT7, Q2), with nested party loops | 204, 214, 315, 322, 990 |
| E | **Financial (BPR/TRN)** | BPR payment + TRN trace header, then party N1s, then remittance detail (RMR / CLP-SVC / ENT) | 820, 835, 823 |
| F | **Warehouse W-segments** | W-prefixed header, detail, and totals segments | 940, 943, 944, 945, 947 |
| G | **Acknowledgment / report** | Nested references to *another* document's control numbers, segments, and elements | 997, 999, 824, 864, TA1 |

Loops that aren't marked in the data (archetypes A, C, D, F) need either a loop-start table per
document (a small amount of data, not code) or the heuristic: a segment ID that repeats with a
consistent set of following segments starts a loop.

---

## 3. Tier 1: the documents that make up most real-world traffic

### Supply chain / retail

#### 850 Purchase Order (GS01 `PO`, archetype A)
```
ST
BEG  purpose(00 original) type(SA stand-alone) PO# date
[CUR REF PER FOB CSH ITD DTM TD5 N9]
{N1 party (ST ship-to, BT bill-to, VN vendor, BY buyer)
   [N2 N3 address N4 city/state/zip PER contact]}
{PO1 line# qty uom price price-basis {id-qualifier id}…   (UP=UPC, VN=vendor part, BP=buyer part)
   [{PID description} PO4 packaging REF SAC DTM SCH {N1…}]}
CTT  line count, hash total
[AMT TT total]
SE
```

#### 855 PO Acknowledgment (`PR`, A)
`BAK` purpose, ack type (AC with detail, AD no detail, AK, RJ rejected), PO#, date → `{N1}` → `{PO1 → {ACK status (IA accepted, IR rejected, IQ qty changed, IB backordered…) qty date}}` → `CTT`

#### 860 PO Change, buyer initiated (`PC`, A) / 865 PO Change Ack, seller initiated (`CA`, A)
`BCH` purpose, type, PO#, date → `{N1}` → `{POC line# change-code (AI add, DI delete, QD qty decrease, PC price change, CA changes) qty …}` → `CTT`

#### 856 Ship Notice / Manifest, the ASN (`SH`, archetype B)
```
BSN  purpose shipment-id date time hierarchy-structure-code
{HL id parent level
   level S = Shipment : TD1 packaging/weight, TD5 carrier, TD3 equipment, REF (BM BOL, CN PRO), DTM, {N1}
   level O = Order    : PRF PO#, PID, REF
   level T = Tare     : MAN GM pallet SSCC
   level P = Pack     : MAN GM carton SSCC, PO4
   level I = Item     : LIN ids, SN1 qty shipped, PID, PO4
}
CTT
```
BSN05 gives the hierarchy order: `0001` = S-O-T-P-I (pick & pack) and `0002` = S-O-I (standard). Partners
often omit levels. Because HL carries parent IDs, this document can always be parsed into a tree.

#### 810 Invoice (`IN`, A)
```
BIG  invoice-date invoice# PO-date PO#
[NTE CUR REF {N1 N3 N4} ITD terms DTM FOB]
{IT1 line# qty uom unit-price basis {qual id}…  [PID REF {SAC}]}
TDS  total amount (implied 2 decimals: 68400 = 684.00)
[CAD {SAC charges/allowances} ISS]
CTT
```

#### 846 Inventory Inquiry/Advice (`IB`, A)
`BIA` → `{N1}` → `{LIN → PID → {QTY (33 available, 17 on hand, 02 committed…) → SCH/DTM}}` → `CTT`

#### 830 Planning Schedule (`PS`, A) / 862 Shipping Schedule (`SS`, A), automotive / manufacturing
`BFR` (830) / `BSS` (862) → `{N1}` → `{LIN → UIT → {FST qty, forecast-code (C firm, D planning), timing, date} → {SHP cumulative received} → [JIT 862 only]}` → `CTT`

#### 852 Product Activity Data (`PD`, A)
`XQ` reporting date range → `{N1}` → `{LIN → {ZA activity type (QS sold, QA on hand, QO out of stock) → {SDQ uom id-qual location qty …}}}` → `CTT`

#### 832 Price/Sales Catalog (`SC`, A)
`BCT` → `{N1}` → `{LIN → {PID} → {CTP price} → PO4 → MEA → DTM}` → `CTT`

### Warehouse (archetype F)

| Doc | GS01 | Header | Detail | Totals |
|---|---|---|---|---|
| 940 Warehouse Shipping Order | `OW` | `W05` order status, depositor order#, PO# + N1/N9/G62/W66 carrier | `{LX → W01 qty uom upc …}` | `W76` |
| 945 Warehouse Shipping Advice | `SW` | `W06` reporting code, depositor order#, date, shipment ID + N1/N9/G62/W27 | `{LX → W12 status (CC complete, CP partial) ordered shipped diff …}` | `W03` |
| 943 Stock Transfer Shipment Advice | `AR` | `W06` + N1/N9/G62/W27 | `{W04 qty uom item}` | `W03` |
| 944 Stock Transfer Receipt Advice | `RE` | `W17` + N1/N9/G62 | `{W07 qty uom item}` | `W14` |
| 947 Inventory Adjustment Advice | `AW` | `W15` + N1 | `{W19 adjustment reason, qty, item}` | — |

### Transportation

#### 204 Motor Carrier Load Tender (`SM`, archetype D)
`B2` SCAC, shipment ID, payment method (PP prepaid / CC collect) → `B2A` purpose (00 new, 01 cancel, 04 change) → `[L11 G62 NTE {N1}]` → `{S5 stop# reason (CL complete load, LD load, CU complete unload, UL unload) → L11 G62 AT8 {N1 N3 N4} {OID}}` → `L3` totals

#### 990 Response to a Load Tender (`GF`, D)
`B1` SCAC, shipment ID, date, action (A accept, D decline) → `[N9 V9]`

#### 214 Shipment Status (`QM`, archetypes C/D)
`B10` ref#, shipment ID, SCAC → `[L11 {N1}]` → `{LX → {AT7 status (X3 arrived at pickup, AF departed, X1 arrived, D1 delivered…) reason date time tz → MS1 location → MS2 equipment} → L11 → AT8}`

#### 210 Freight Details and Invoice (`IM`, C)
`B3` invoice#, shipment ID, payment method, net amount, dates, SCAC → `C3 N9 G62 R3 {N1}` → `{LX → L5 description → L0 weight → {L1 charges (rate, amount, code e.g. 400 freight, FUE fuel)} → L4/L7}` → `L3` totals

### Finance

#### 820 Payment Order / Remittance Advice (`RA`, archetype E)
```
BPR  handling-code amount credit/debit method(ACH, CHK, FWT) format(CCP, CTX) bank-routing… date
TRN  trace/check #
[CUR REF DTM]
{N1  PR payer / PE payee}
{ENT  entity
   {RMR  ref-qual (IV invoice#) amount-paid amount-invoiced discount
      [REF DTM {ADX adjustment}]}}
```
In HIPAA 5010 (`005010X218`, premium payment) the detail sits in `{ENT → NM1 → {RMR → DTM}}` loops.

#### 824 Application Advice (`AG`, archetype G)
`BGN` → `{N1}` → `{OTI accept/reject code, reference to the original doc (GS/ST control numbers, PO#, invoice#) → {TED error code + message} → NTE}`. This is the business-level rejection, as opposed to the 997, which is syntax-level.

### Healthcare (HIPAA, archetype B)

HIPAA documents are **guide-driven**: the guide ID (GS08 / ST03) controls which loops are allowed.
Each loop is identified by a qualifier inside its first segment. For example, NM1\*85 is the billing provider and NM1\*IL is the subscriber.

#### 837 Health Care Claim (`HC`): P (Professional), I (Institutional), D (Dental)
```
BHT  0019 purpose(00 original, 18 reissue) batch# date time type(CH chargeable, RP reporting)
1000A NM1*41 Submitter [PER]
1000B NM1*40 Receiver
2000A HL*n**20   Billing provider      [PRV]
  2010AA NM1*85  Billing provider name/NPI, N3, N4, REF*EI tax ID
  [2010AB NM1*87 Pay-to address]
  2000B HL*n*p*22  Subscriber        SBR payer responsibility, relationship, group, filing code
    2010BA NM1*IL  Subscriber name/ID, N3, N4, DMG
    2010BB NM1*PR  Payer
    [2000C HL*n*p*23  Patient (only if not the subscriber): PAT, NM1*QC, DMG]
      2300 CLM claim-id total-charge … place-of-service:B:freq …
           DTP, REF, HI diagnosis codes, [CL1 inst], [2310x NM1 referring/rendering/facility], [2320 other payer]
        {2400 LX → SV1 (P) | SV2 (I) | SV3 (D)  procedure charge units → DTP*472 service date → REF}
```
**837 differences between 4010 and 5010:**
- 4010 uses ICD-9 diagnosis codes (HI qualifier `BK`). 5010 is required for ICD-10 (qualifier `ABK`).
- The billing provider must be an organization or person with an NPI, and its address can't be a P.O. box.
- 5010 requires 9-digit ZIP codes on the billing provider and service facility.
- PRV taxonomy uses qualifier `PXC` instead of `ZZ`.
- CLM elements were tightened; 4010's CLM11–20 are mostly gone.
- Many secondary-ID REF segments were removed.

#### 835 Health Care Claim Payment/Advice, the ERA (`HP`, archetype E plus C)
```
BPR payment (amount, method ACH/CHK/NON, bank info, date)   TRN check/EFT trace   [CUR REF DTM*405]
1000A N1*PR Payer  [N3 N4 REF PER]
1000B N1*PE Payee  [N3 N4 REF]
{2000 LX  [TS3 TS2 provider summary]
   {2100 CLP claim-id status(1 primary, 2 secondary, 4 denied, 22 reversal) charge paid patient-resp filing payer-ctrl#
         {CAS group(CO contractual, PR patient resp, OA other, PI payer initiated) reason amount …}
         NM1*QC patient, NM1*82 rendering, DTM*232/233, AMT
      {2110 SVC procedure charge paid revenue-code units → DTM*472 → {CAS} → REF → AMT → {LQ*HE remark code}}}}
[{PLB provider-level adjustments}]
```

#### 834 Benefit Enrollment and Maintenance (`BE`)
`BGN` (BGN08: 2 change, 4 verify/full file) → `REF*38 DTP` → `1000A N1*P5 sponsor` → `1000B N1*IN payer` → `{2000 INS member (Y subscriber / N dependent, relationship 18 self 01 spouse 19 child, maintenance 021 add 024 cancel 001 change 030 audit) → REF*0F → DTP → 2100A NM1*IL N3 N4 DMG → {2300 HD coverage (HLT, DEN, VIS) plan level → DTP*348/349}}`

#### 270 / 271 Eligibility Inquiry / Response (`HS` / `HB`)
`BHT*0022*13` (request) or `*11` (response) → `HL 20` info source (NM1\*PR payer) → `HL 21` receiver (NM1\*1P provider) → `HL 22` subscriber (TRN, NM1\*IL, DMG, DTP) → `[HL 23 dependent]`.
- 270 ends with `{EQ service type (30 general, 98 office visit, …)}`.
- 271 returns `{EB eligibility (1 active, 6 inactive, A co-insurance, B co-pay, C deductible) coverage-level service-type insurance-type plan … amount percent}` with `MSG` and `AAA` errors.

#### 276 / 277 Claim Status Request / Response (`HR` / `HN`)
`BHT*0010` → `HL 20` payer → `HL 21` receiver → `HL 19` provider → `HL 22` subscriber → `[HL 23 dependent]`.
- 276 claim detail: `TRN REF AMT DTP`.
- 277 adds `STC` status `category:code` (A1/A2 acknowledged, P1 pending, F1 paid, F2 denied, …), amounts, and payment info.
- The 277CA (`005010X214`) is the front-end claim acknowledgment variant.

#### 278 Services Review: prior auth / referral (`HI`)
`BHT*0007` → HL levels 20 (UMO), 21 (requester), 22 (subscriber), 23 (dependent), EV (patient event), SS (service) → `UM` request type, `HCR` decision, `HI`, `HSD`, `CRC`.

### Acknowledgments (archetype G)

#### 997 Functional Acknowledgment (`FA`), 4010 and 5010
```
AK1  group-type(GS01) group-control#(GS06)
{AK2 set-id(ST01) set-control#(ST02)
   {AK3 segment-id position loop-id error-code
      {AK4 element-position element-ref error-code bad-value}}
   AK5 set status (A accepted, E accepted with errors, R rejected) [error codes]}
AK9  group status (A, E, P partial, R) sets-included sets-received sets-accepted
```

#### 999 Implementation Acknowledgment (`FA`), 5010 only
Same structure as the 997, with these differences: AK1/AK2 carry the guide version; `IK3`/`IK4` replace `AK3`/`AK4`; `CTX` adds business or segment context (e.g. the claim ID); `IK5` replaces `AK5`. It reports errors against the **implementation guide**, not just the base standard.

#### TA1 Interchange Acknowledgment
This is a standalone segment between ISA and IEA, not an ST/SE document: `TA1*ISA13*date*time*ack(A/E/R)*note-code`. **The parser must allow segments directly inside ISA with no GS group.**

---

## 4. Tier 2 and 3

The JSON registry flags 28 **tier 2** sets as common within specific industries. Examples:
- Ocean: 300/301/304/310/315
- Rail: 404/410
- Grocery and DSD: 875/880/894
- Quality and engineering: 842/863
- Returns: 180
- Healthcare attachments: 275
- Text messages: 864

The remaining ~270 **tier 3** sets are niche:
- Mortgage, education, and government filings (most of X12F 1xx/2xx)
- Rail operations (4xx)
- Customs (3xx)
- Insurance underwriting

For these, the plan is a **generic parse** (envelope tree, segments, plus HL and heuristic loop
inference) with segment- and element-level labels from the dictionary, and no document-specific schema.

---

## 5. Version differences to model

| Area | 004010 | 005010 |
|---|---|---|
| Transaction sets | 293 | 318 (+26 new incl. 999, 274, 269, 753/754, 873/874; 218 dropped) |
| Repetition separator | none | ISA11, used e.g. in HIPAA HI/CLM composites |
| HIPAA guides | X091–X098 (A1 addenda) | X212–X231 (A1/A2 errata) |
| Diagnosis codes | ICD-9 (`BK`, `BF`) | ICD-10 (`ABK`, `ABF`) |
| Element lengths | shorter (e.g. NM103 35 chars) | many expanded (NM103 60 chars) |
| Segment & code lists | — | segments and code values added or removed per document; schemas must be keyed by **GS08** |

Non-HIPAA retail and transport documents (850, 810, 856, 204, 214…) changed very little between
4010 and 5010. Most of those changes are new optional elements or codes, so one tolerant schema per document can cover both
versions. HIPAA documents need separate per-guide schemas.

---

## 6. Related standards (not X12)

The parser will later meet these. They have the same delimited-segment syntax, so the tokenizer can be reused:

| Standard | Envelope | Examples |
|---|---|---|
| UN/EDIFACT | `UNA` `UNB` [`UNG`] `UNH`…`UNT` [`UNE`] `UNZ` | ORDERS (850), ORDRSP (855), DESADV (856), INVOIC (810), REMADV (820), IFTSTA (214), CONTRL (997) |
| TRADACOMS | `STX`…`MHD`…`MTR`…`END` | ORDHDR/ORDERS, INVFIL/INVOIC |
| HL7 v2 | `MSH` + segments, `\r` terminated | ADT, ORM, ORU |

The `edifact_equivalent` field in the JSON registry maps X12 documents to their EDIFACT counterparts.
