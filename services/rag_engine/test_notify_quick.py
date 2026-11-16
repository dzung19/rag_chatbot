from compare_engine import (
    extract_notify_party_comparison,
)

from compare_engine import (
    build_shipping_dates_reference_banner,
)

checklist_text = """
NOTIFY PARTY (1) SAP CODE: ZZE0LOGI
BROTHER INTERNATIONAL EUROPE LTD.
BROTHER HOUSE, 1 TAME STREET,
GUIDE BRIDGE,
AUDENSHAW, MANCHESTER,
M34 5JE, ENGLAND
TEL +44(0)7795408300
EMAIL: AGNIESZKA.CALKA-BONCZAK@BROTHER.CO.UK
VAT+864 4502 20

NOTIFY PARTY (2) SAP CODE
DELIVERY TO
FM LOGISTIC
"""


bl_text = """
NotifyParty | websiteathttps:/www.yusen-logistics.com/services/
BROTHER INTERNATIONAL EUROPE LTD.
BROTHER HOUSE，1TAME STREET，GUIDE BRIDGE，
AUDENSHAW,MANCHESTER,
M345JE,ENGLAND TEL+44(0)7795408300
EMAIL:AGNIESZKA.CALKA-BONCZAK@BROTHER.CO.UK
VAT+864450220
ForwardingAgentReference
"""


results = extract_notify_party_comparison(
    checklist_text,
    bl_text,
)

for result in results:
    print("=" * 70)
    print("Notify Party:", result["number"])
    print("Checklist:", result["checklist_value"])
    print("BL:", result["bl_value"])
    print("Status:", result["status"])
    print("Matched:", result["matched_tokens"])
    print("Detail:", result["detail"])



print("\n" + "#" * 80)
print("BANNER")
print("#" * 80)

banner = build_shipping_dates_reference_banner(
    checklist_text,
    bl_text,
)

print(banner)