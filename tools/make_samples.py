"""Generate synthetic X12 sample files (004010 and 005010) for parser testing.

All names, IDs and amounts are fictitious. Envelopes (ISA/GS/SE/GE/IEA) are
built programmatically so fixed widths and control counts are always valid.
Run: python3 tools/make_samples.py
"""
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent / "samples" / "x12"


def isa(version, ctrl, elem, rep, comp, seg_term, sender="SENDERID", receiver="RECEIVERID"):
    isa11 = rep if version == "00501" else "U"
    fields = ["ISA", "00", " " * 10, "00", " " * 10, "ZZ", sender.ljust(15), "ZZ", receiver.ljust(15),
              "261003", "1200", isa11, version, f"{ctrl:09d}", "0", "T", comp]
    s = elem.join(fields) + seg_term
    assert len(s) == 106, len(s)
    return s


def build(name, version, gs01, gs08, sets, elem="*", rep="^", comp=":", seg="~", newline="\n"):
    """sets: list of (st01, [segment strings using '*' and ':' placeholders, excluding ST/SE])."""
    term = seg + newline
    ctrl = 1
    out = [isa(version, ctrl, elem, rep, comp, seg) + newline]
    out.append(f"GS*{gs01}*SENDERID*RECEIVERID*20261003*1200*1*X*{gs08}" + term)
    for i, (st01, body) in enumerate(sets, 1):
        # ST03 (implementation convention reference) exists only in 005010+
        st = f"ST*{st01}*{i:04d}" + (f"*{gs08}" if version == "00501" and "X" in gs08 else "")
        segs = [st] + body
        segs.append(f"SE*{len(segs) + 1}*{i:04d}")
        out.extend(s + term for s in segs)
    out.append(f"GE*{len(sets)}*1" + term)
    out.append(f"IEA*1*{ctrl:09d}" + term)
    text = out[0] + "".join(out[1:]).replace("*", elem).replace(":", comp)
    path = ROOT / ("4010" if version == "00401" else "5010") / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
    return path


# ---------------------------------------------------------------- supply chain
PO_850 = [
    "BEG*00*SA*PO-10045**20261001",
    "CUR*BY*USD",
    "REF*DP*042",
    "PER*BD*JANE BUYER*TE*5555550100",
    "FOB*PP",
    "ITD*01*3*2**10**30",
    "DTM*002*20261015",
    "TD5****M*BEST WAY",
    "N1*ST*EXAMPLE DC 7*92*0007",
    "N3*100 WAREHOUSE RD",
    "N4*COLUMBUS*OH*43004*US",
    "N1*VN*ACME SUPPLY CO*92*V12345",
    "PO1*1*24*EA*12.50*PE*UP*012345678905*VN*AC-100",
    "PID*F****WIDGET, BLUE, LARGE",
    "PO4*6*1*EA",
    "PO1*2*10*CA*48.00*PE*UP*012345678912*VN*AC-200",
    "PID*F****GADGET CASE OF 12",
    "CTT*2*34",
    "AMT*TT*780.00",
]
ACK_855 = [
    "BAK*00*AC*PO-10045*20261001****20261002",
    "N1*ST*EXAMPLE DC 7*92*0007",
    "PO1*1*24*EA*12.50*PE*UP*012345678905*VN*AC-100",
    "ACK*IA*24*EA*068*20261015",
    "PO1*2*10*CA*48.00*PE*UP*012345678912*VN*AC-200",
    "ACK*IQ*8*CA*068*20261015",
    "CTT*2",
]
ASN_856 = [  # Standard Pick & Pack: Shipment > Order > Pack > Item
    "BSN*00*SHP-88001*20261012*1530*0001",
    "HL*1**S",
    "TD1*CTN25*2****G*130*LB",
    "TD5*B*2*ABCD*M*ABC FREIGHT",
    "REF*BM*BOL-55501",
    "DTM*011*20261012",
    "N1*ST*EXAMPLE DC 7*92*0007",
    "N1*SF*ACME SUPPLY CO*92*V12345",
    "HL*2*1*O",
    "PRF*PO-10045***20261001",
    "HL*3*2*P",
    "MAN*GM*00001234560000000018",
    "HL*4*3*I",
    "LIN*1*UP*012345678905*VN*AC-100",
    "SN1**24*EA",
    "HL*5*2*P",
    "MAN*GM*00001234560000000025",
    "HL*6*5*I",
    "LIN*2*UP*012345678912*VN*AC-200",
    "SN1**8*CA",
    "CTT*6",
]
INV_810 = [
    "BIG*20261013*INV-77001*20261001*PO-10045",
    "CUR*SE*USD",
    "REF*BM*BOL-55501",
    "N1*RE*ACME SUPPLY CO*92*V12345",
    "N3*1 FACTORY WAY",
    "N4*DAYTON*OH*45402",
    "N1*ST*EXAMPLE DC 7*92*0007",
    "ITD*01*3*2**10**30",
    "DTM*011*20261012",
    "IT1*1*24*EA*12.50**UP*012345678905*VN*AC-100",
    "PID*F****WIDGET, BLUE, LARGE",
    "IT1*2*8*CA*48.00**UP*012345678912*VN*AC-200",
    "PID*F****GADGET CASE OF 12",
    "TDS*68400",
    "SAC*C*D240***2500",
    "CTT*2",
]
FA_997 = [
    "AK1*PO*1",
    "AK2*850*0001",
    "AK5*A",
    "AK2*850*0002",
    "AK3*PO1*14**8",
    "AK4*4*212*4*ABC",
    "AK5*R*5",
    "AK9*P*2*2*1",
]
WSO_940 = [
    "W05*N*ORD-3001*PO-10045",
    "N1*ST*CUSTOMER STORE 12*92*S12",
    "N3*45 MAIN ST",
    "N4*AUSTIN*TX*78701",
    "N9*CO*CUST-ORD-9",
    "G62*10*20261014",
    "W66*PP*M***ABCD",
    "LX*1",
    "W01*24*EA*012345678905*VN*AC-100",
    "LX*2",
    "W01*8*CA*012345678912*VN*AC-200",
    "W76*32*130*LB",
]
WSA_945 = [
    "W06*N*ORD-3001*20261014*SHP-9001**PO-10045",
    "N1*ST*CUSTOMER STORE 12*92*S12",
    "N9*BM*BOL-66001",
    "G62*11*20261014",
    "W27*M*ABCD",
    "LX*1",
    "W12*CC*24*24**EA*012345678905*VN*AC-100",
    "LX*2",
    "W12*CP*8*6*2*CA*012345678912*VN*AC-200",
    "W03*30*120*LB",
]
# ---------------------------------------------------------------- transportation
TENDER_204 = [
    "B2**ABCD**LOAD-501**PP",
    "B2A*00",
    "L11*PO-10045*PO",
    "G62*64*20261012",
    "N1*BT*EXAMPLE RETAIL INC",
    "S5*1*CL",
    "G62*69*20261012*U*0800",
    "N1*SH*ACME SUPPLY CO",
    "N3*1 FACTORY WAY",
    "N4*DAYTON*OH*45402",
    "S5*2*CU",
    "G62*70*20261013*X*1400",
    "N1*CN*EXAMPLE DC 7",
    "N3*100 WAREHOUSE RD",
    "N4*COLUMBUS*OH*43004",
    "L3*130*G***45000",
]
STATUS_214 = [
    "B10*PRO-12345*LOAD-501*ABCD",
    "L11*PO-10045*PO",
    "N1*SH*ACME SUPPLY CO",
    "N1*CN*EXAMPLE DC 7",
    "LX*1",
    "AT7*AF*NS***20261012*1015*ET",
    "MS1*DAYTON*OH*US",
    "LX*2",
    "AT7*D1*NS***20261013*1340*ET",
    "MS1*COLUMBUS*OH*US",
]
FREIGHT_210 = [
    "B3**PRO-12345*LOAD-501*PP**20261014*48500**20261013*035*ABCD",
    "C3*USD",
    "N9*PO*PO-10045",
    "N1*SH*ACME SUPPLY CO",
    "N1*CN*EXAMPLE DC 7",
    "N1*BT*EXAMPLE RETAIL INC",
    "LX*1",
    "L5*1*GENERAL MERCHANDISE",
    "L0*1***130*G***2*CTN",
    "L1*1***45000****400",
    "L1*2***3500****FUE",
    "L3*130*G***48500",
]
# ---------------------------------------------------------------- healthcare
def claim_837p(version):
    v5 = version == "00501"
    return [
        "BHT*0019*00*BATCH0001*20261003*1200*CH",
        "NM1*41*2*EXAMPLE BILLING SERVICE*****46*SUB12345",
        "PER*IC*BILLING DEPT*TE*5555550111",
        "NM1*40*2*EXAMPLE PAYER CLEARINGHOUSE*****46*RCV999",
        "HL*1**20*1",
        "PRV*BI*PXC*207Q00000X" if v5 else "PRV*BI*ZZ*207Q00000X",
        "NM1*85*2*EXAMPLE FAMILY CLINIC*****XX*1234567893",
        "N3*200 HEALTH PKWY",
        "N4*MADISON*WI*537030000" if v5 else "N4*MADISON*WI*53703",
        "REF*EI*123456789",
        "HL*2*1*22*0",
        "SBR*P*18*GRP001******CI",
        "NM1*IL*1*DOE*JOHN****MI*MBR0001",
        "N3*12 ELM ST",
        "N4*MADISON*WI*537040000" if v5 else "N4*MADISON*WI*53704",
        "DMG*D8*19800115*M",
        "NM1*PR*2*EXAMPLE HEALTH PLAN*****PI*PAYER01",
        "CLM*CLM-0001*150***11:B:1*Y*A*Y*Y" + ("" if v5 else "*C"),
        "HI*ABK:J069" if v5 else "HI*BK:4659",
        "NM1*82*1*SMITH*ANNA****XX*1987654321",
        "PRV*PE*PXC*207Q00000X" if v5 else "PRV*PE*ZZ*207Q00000X",
        "LX*1",
        "SV1*HC:99213*100*UN*1***1",
        "DTP*472*D8*20260928",
        "LX*2",
        "SV1*HC:87880*50*UN*1***1",
        "DTP*472*D8*20260928",
    ]


def remit_835(version):
    return [
        "BPR*I*120*C*ACH*CCP*01*999999999*DA*123456*1512345678**01*888888888*DA*654321*20261010",
        "TRN*1*EFT-00099*1512345678",
        "DTM*405*20261010",
        "N1*PR*EXAMPLE HEALTH PLAN",
        "N3*1 INSURANCE PLAZA",
        "N4*HARTFORD*CT*06103",
        "PER*BL*PROVIDER SERVICES*TE*5555550199",
        "N1*PE*EXAMPLE FAMILY CLINIC*XX*1234567893",
        "LX*1",
        "CLP*CLM-0001*1*150*120*20*12*PAYERCTRL001*11*1",
        "NM1*QC*1*DOE*JOHN****MI*MBR0001",
        "NM1*82*1*SMITH*ANNA****XX*1987654321",
        "DTM*232*20260928",
        "SVC*HC:99213*100*85**1",
        "DTM*472*20260928",
        "CAS*CO*45*5",
        "CAS*PR*3*10",
        "SVC*HC:87880*50*35**1",
        "DTM*472*20260928",
        "CAS*CO*45*5",
        "CAS*PR*2*10",
        "LQ*HE*N130",
    ]


ENROLL_834 = [
    "BGN*00*ENR-0001*20261003*1200****2",
    "REF*38*GRP001",
    "DTP*007*D8*20261101",
    "N1*P5*EXAMPLE EMPLOYER INC*FI*123456789",
    "N1*IN*EXAMPLE HEALTH PLAN*FI*987654321",
    "INS*Y*18*021*28*A***FT",
    "REF*0F*EMP0001",
    "DTP*356*D8*20261101",
    "NM1*IL*1*DOE*JANE****34*123456789",
    "PER*IP**TE*5555550123",
    "N3*12 ELM ST",
    "N4*MADISON*WI*53704",
    "DMG*D8*19850320*F",
    "HD*021**HLT*PPO GOLD*FAM",
    "DTP*348*D8*20261101",
    "INS*N*19*021*28*A",
    "REF*0F*EMP0001",
    "NM1*IL*1*DOE*SAM",
    "DMG*D8*20150610*M",
    "HD*021**HLT*PPO GOLD*FAM",
    "DTP*348*D8*20261101",
]
ELIG_270 = [
    "BHT*0022*13*INQ-0001*20261003*1200",
    "HL*1**20*1",
    "NM1*PR*2*EXAMPLE HEALTH PLAN*****PI*PAYER01",
    "HL*2*1*21*1",
    "NM1*1P*2*EXAMPLE FAMILY CLINIC*****XX*1234567893",
    "HL*3*2*22*0",
    "TRN*1*TRACE-0001*9123456789",
    "NM1*IL*1*DOE*JOHN****MI*MBR0001",
    "DMG*D8*19800115",
    "DTP*291*D8*20261003",
    "EQ*30",
]
ELIG_271 = [
    "BHT*0022*11*INQ-0001*20261003*1201",
    "HL*1**20*1",
    "NM1*PR*2*EXAMPLE HEALTH PLAN*****PI*PAYER01",
    "HL*2*1*21*1",
    "NM1*1P*2*EXAMPLE FAMILY CLINIC*****XX*1234567893",
    "HL*3*2*22*0",
    "TRN*2*TRACE-0001*9123456789",
    "NM1*IL*1*DOE*JOHN****MI*MBR0001",
    "DMG*D8*19800115*M",
    "DTP*346*D8*20260101",
    "EB*1*IND*30**PPO GOLD",
    "EB*B*IND*98*****25",
    "EB*C*IND*30****23*500",
    "EB*A*IND*30*******.2",
    "MSG*COPAY APPLIES TO OFFICE VISITS",
]
STAT_276 = [
    "BHT*0010*13*STAT-0001*20261003*1200",
    "HL*1**20*1",
    "NM1*PR*2*EXAMPLE HEALTH PLAN*****PI*PAYER01",
    "HL*2*1*21*1",
    "NM1*41*2*EXAMPLE BILLING SERVICE*****46*SUB12345",
    "HL*3*2*19*1",
    "NM1*1P*2*EXAMPLE FAMILY CLINIC*****XX*1234567893",
    "HL*4*3*22*0",
    "DMG*D8*19800115*M",
    "NM1*IL*1*DOE*JOHN****MI*MBR0001",
    "TRN*1*CLM-0001",
    "AMT*T3*150",
    "DTP*472*RD8*20260928-20260928",
]
STAT_277 = [
    "BHT*0010*08*STAT-0001*20261004*0900*DG",
    "HL*1**20*1",
    "NM1*PR*2*EXAMPLE HEALTH PLAN*****PI*PAYER01",
    "HL*2*1*21*1",
    "NM1*41*2*EXAMPLE BILLING SERVICE*****46*SUB12345",
    "HL*3*2*19*1",
    "NM1*1P*2*EXAMPLE FAMILY CLINIC*****XX*1234567893",
    "HL*4*3*22*0",
    "NM1*IL*1*DOE*JOHN****MI*MBR0001",
    "TRN*2*CLM-0001",
    "STC*F1:65*20261010**150*120*20261010*ACH*20261010*EFT-00099",
    "REF*1K*PAYERCTRL001",
    "DTP*472*RD8*20260928-20260928",
]
PAY_820 = [
    "BPR*C*780*C*ACH*CTX*01*111000025*DA*1234567*1234567890**01*222000111*DA*7654321*20261020",
    "TRN*1*PAY-20261020-01*1234567890",
    "CUR*PR*USD",
    "DTM*097*20261020",
    "N1*PR*EXAMPLE RETAIL INC",
    "N1*PE*ACME SUPPLY CO*92*V12345",
    "ENT*1",
    "RMR*IV*INV-77001**684.00*684.00",
    "DTM*003*20261013",
    "RMR*IV*INV-77002**96.00*100.00*4.00",
    "ADX*-4.00*01",
]
ACK_999 = [
    "AK1*HC*1*005010X222A1",
    "AK2*837*0001*005010X222A1",
    "IK5*A",
    "AK2*837*0002*005010X222A1",
    "IK3*NM1*8*2010AA*8",
    "CTX*CLM01:CLM-0002",
    "IK4*9*67*7*123",
    "IK5*R*5",
    "AK9*P*2*2*1",
]


def main():
    made = [
        # 004010 — classic '*' / '~' + newline
        build("850_purchase_order.edi", "00401", "PO", "004010", [("850", PO_850)]),
        build("855_po_acknowledgment.edi", "00401", "PR", "004010", [("855", ACK_855)]),
        build("856_ship_notice.edi", "00401", "SH", "004010", [("856", ASN_856)]),
        build("810_invoice.edi", "00401", "IN", "004010", [("810", INV_810)]),
        build("997_functional_ack.edi", "00401", "FA", "004010", [("997", FA_997)]),
        build("940_warehouse_shipping_order.edi", "00401", "OW", "004010", [("940", WSO_940)]),
        build("945_warehouse_shipping_advice.edi", "00401", "SW", "004010", [("945", WSA_945)]),
        build("204_load_tender.edi", "00401", "SM", "004010", [("204", TENDER_204)]),
        build("214_shipment_status.edi", "00401", "QM", "004010", [("214", STATUS_214)]),
        build("210_freight_invoice.edi", "00401", "IM", "004010", [("210", FREIGHT_210)]),
        build("837P_professional_claim.edi", "00401", "HC", "004010X098A1", [("837", claim_837p("00401"))]),
        build("835_claim_payment.edi", "00401", "HP", "004010X091A1", [("835", remit_835("00401"))]),
        # 004010 delimiter variant: '|' elements, '>' components, no newlines
        build("850_purchase_order_pipe_delims.edi", "00401", "PO", "004010", [("850", PO_850), ("850", PO_850)],
              elem="|", comp=">", newline=""),
        # 005010
        build("850_purchase_order.edi", "00501", "PO", "005010", [("850", PO_850)]),
        build("856_ship_notice.edi", "00501", "SH", "005010", [("856", ASN_856)]),
        build("810_invoice.edi", "00501", "IN", "005010", [("810", INV_810)]),
        build("820_payment_remittance.edi", "00501", "RA", "005010", [("820", PAY_820)]),
        build("999_implementation_ack.edi", "00501", "FA", "005010X231A1", [("999", ACK_999)]),
        build("837P_professional_claim.edi", "00501", "HC", "005010X222A1", [("837", claim_837p("00501"))]),
        build("835_claim_payment.edi", "00501", "HP", "005010X221A1", [("835", remit_835("00501"))]),
        build("834_benefit_enrollment.edi", "00501", "BE", "005010X220A1", [("834", ENROLL_834)]),
        build("270_eligibility_inquiry.edi", "00501", "HS", "005010X279A1", [("270", ELIG_270)]),
        build("271_eligibility_response.edi", "00501", "HB", "005010X279A1", [("271", ELIG_271)]),
        build("276_claim_status_request.edi", "00501", "HR", "005010X212", [("276", STAT_276)]),
        build("277_claim_status_response.edi", "00501", "HN", "005010X212", [("277", STAT_277)]),
        # 005010 delimiter variant: '|' elements, '!' repetition separator, CRLF line endings
        build("837P_professional_claim_alt_delims.edi", "00501", "HC", "005010X222A1", [("837", claim_837p("00501"))],
              elem="|", rep="!", comp=":", seg="~", newline="\r\n"),
    ]
    for p in made:
        print(p.relative_to(ROOT.parent.parent))


if __name__ == "__main__":
    main()
