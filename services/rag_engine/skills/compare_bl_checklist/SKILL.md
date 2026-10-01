# BL vs BL Check List Comparison

## Purpose
Compare the user-provided **BL** against the user-provided **BL Check List**. Treat the BL Check List as the reference, except that SHIPPER must be checked against the standard SHIPPER data defined in this skill. Report completely and accurately. Never guess.

## Mandatory rules
- Start only when both corresponding files or contents are available.
- When data exists in both sources, compare it and conclude **CORRECT** or **INCORRECT**.
- When data is missing from either source, conclude **NOT FOUND**.
- When the comparison method cannot be determined, conclude **NOT UNDERSTOOD**.
- Never infer data from BL to BL Check List or from BL Check List to BL.
- Ignore differences that are only spacing or formatting in CONSIGNEE, NOTIFY PARTY 1, NOTIFY PARTY 2, and DELIVERY TO.
- If long content is continued with `*`, `>>`, or a similar continuation marker, locate and join all continued parts before comparing.
- For every incorrect point, bold both the differing data and the word **INCORRECT**.

## Standard SHIPPER data
BROTHER INDUSTRIES (VIETNAM) LTD. PHUC DIEN INDUSTRIAL ZONE, MAO DIEN COMMUNE, HAI PHONG CITY, VIETNAM TEL NO.84-2203-545 860 FAX:545 878 MS. HARUNA WATANABE TRN+0800304173.

## Field mapping rules
- FROM ↔ PORT OF LOADING.
- VIA ↔ PORT OF DISCHARGE.
- TO ↔ PLACE OF DELIVERY.
- REQUEST NO. ↔ SHIPMENT / SHIPMENT NO.
- NOTIFY PARTY 2 ↔ ALSO NOTIFY PARTY.
- ETD HPH ON in REQUEST ↔ ETD on BL.
- ISSUE DATE must equal ETD.
- SDD on BL Check List ↔ DOOR on BL. Example: `CY / BADEN SDD` ↔ `CY/DOOR`.

## Inspection procedure

### 1. Read and normalize
Identify every page, invoice, container, and continued information block. Preserve original values for evidence and presentation.

### 2. Bill and invoice numbers
- For every INVOICE NO. line, take the substring from the numeric portion through the end of the letters/numbers on that line. Example: `824957-0`.
- Verify that every BILL OF LADING (AIR WAYBILL) line contains a bill number and that it matches the BL.
- On the summary page containing G-TOTAL, verify that all invoices from preceding pages are included and that no extra invoice exists.
- Interpret abbreviated notation such as `824957-0 1` as `824957-0` and `824957-1`.

### 3. B/L REQUIRED
- On BL, `ON BOARD Date + date` means ON BOARD.
- On BL, `RECEIVED DATE + date` means RECEIVED.
- Compare this with B/L REQUIRED on the Check List.
- BL is valid only when exactly one date type appears. If both types appear, conclude **INCORRECT**.

### 4. Description of goods

#### 4.1 Standard descriptions from each invoice on BL Check List
- Take all content immediately below INVOICE NO. and above the first TTL line.
- Treat each goods-description line as a separate description.

#### 4.2 Build the standard unique list
- Combine descriptions from all invoices.
- Remove only exact duplicate lines.
- The result is the single standard unique GOODS DESCRIPTION list.

#### 4.3 Actual descriptions from BL
1. Read descriptions by container and preserve every occurrence.
2. Join descriptions split across lines, pages, or continuation markers.
3. Do not deduplicate or merge the BL list.
4. Record the occurrence count and containers for every description.

#### 4.4 Required validation order
1. Build the unique standard list from BL Check List.
2. Extract the actual BL list while preserving duplicates.
3. Count BL occurrences before any deduplication.
4. Every standard description must occur exactly once across the entire BL, not once per container.
5. Zero occurrences → **INCORRECT: missing**.
6. Two or more occurrences, including across different containers → **INCORRECT: duplicate**.
7. A BL description absent from the standard list → **INCORRECT: extra**.
8. Conclude **CORRECT** only when every standard description occurs exactly once and BL has no extra description.

#### 4.5 Required evidence
- Show every description, occurrence count, and corresponding container list.
- Never conclude using a comparison of two already-deduplicated sets.
- For data set 830753, `LASER PRINTER`, `DIGITAL COPIER/PRINTER`, and `MULTI-FUNCTIONAL COPIER/PRINTER/FAX` are repeated, so GOODS DESCRIPTION must be **INCORRECT**.

#### 4.6 Result table
- Show the standard deduplicated GOODS DESCRIPTION list from BL Check List.
- Show the actual BL GOODS DESCRIPTION occurrences.
- If incorrect, identify every missing, extra, or repeated description.

### 5. Compare all items
1. SHIPPER against the standard SHIPPER data.
2. CONSIGNEE.
3. NOTIFY PARTY 1.
4. NOTIFY PARTY 2.
5. DELIVERY TO.
6. CONTAINER NO. for each container.
7. SEAL NO. for each container.
8. CONTAINER TYPE for each container.
9. P'KGS for each container.
10. WEIGHT for each container.
11. MEASUREMENT/M3 for each container.
12. Total P'KGS, WEIGHT, and MEASUREMENT/M3 across all containers.
13. GOODS DESCRIPTION.
14. PORT OF LOADING.
15. PORT OF DISCHARGE.
16. PLACE OF DELIVERY.
17. ETD.
18. ISSUE DATE = ETD.
19. Total number of containers and container types, for example `2x40 HC`.
20. Delivery term: CY-CY, CY-DOOR, CFS-CFS, or CFS-DOOR.
21. SHIPMENT NO.
22. B/L REQUIRED: ON BOARD or RECEIVED according to the rules above.
23. HS code: BL must not display an HS code. If it does, conclude **INCORRECT**.

### 6. Count results
Count CORRECT, INCORRECT, NOT UNDERSTOOD, and NOT FOUND separately. Every container and every container attribute is a separate inspection point.

## Output workbook
Create one `.xlsx` workbook with exactly two sheets. Use clear headers, enable filters, and set readable column widths.

### Sheet 1: CORRECT ITEMS
Include only CORRECT items.
Columns:
- File
- Check item
- BL Check List data
- BL data
- Result

Every container and every container attribute must be a separate row. Add a final total row showing the total CORRECT count for each file.

### Sheet 2: ITEMS REQUIRING ACTION
Include every INCORRECT, NOT FOUND, and NOT UNDERSTOOD item.
Columns:
- File
- Check item
- BL Check List data
- BL data
- Result
- Required action

Clearly state the differing portion, missing data, reason the comparison is not understood, or action the user must perform.
Color results:
- INCORRECT: red
- NOT FOUND: orange
- NOT UNDERSTOOD: yellow

Add final totals for INCORRECT, NOT FOUND, and NOT UNDERSTOOD for each file.

At the top of ITEMS REQUIRING ACTION, include this exact uppercase bold reminder:

**BẠN HÃY TỰ CHECK LOẠI BL, NUMBER OF ORINAL BL, ETA VIA, ETA TO VÀ CÁC ĐIỂM ĐẶC BIỆT DÀNH CHO THỊ TRƯỜNG MÀ BẠN ĐANG CHECK BL**

## Completion
- After inspection, create and attach the Excel workbook.
- If multiple BL files are provided, keep exactly two sheets and distinguish each BL in the File column.

## Error handling
- If a file cannot be read or BL cannot be distinguished from BL Check List, identify the file and use **NOT FOUND** for data that cannot be extracted.
- If a new layout does not match these rules, use **NOT UNDERSTOOD** instead of interpreting it.
- If multiple BL files exist, consolidate all results in one workbook and distinguish them by File.

## Grounding restriction
Use only evidence returned by the comparison/extraction tools. If the tool output does not contain enough evidence to apply a rule, return NOT FOUND or NOT UNDERSTOOD. Never claim that a workbook was created unless a real downloadable workbook artifact was generated.
