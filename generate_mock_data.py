import json
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
POC_DATA = BASE_DIR / "poc_data"

# Ensure directories
for folder in ["los/loans", "los/scorecards_received", "dms", "s3_los", "s3_raw", "s3_extracted", "s3_extracted_structured", "s3_result"]:
    (POC_DATA / folder).mkdir(parents=True, exist_ok=True)

# Minimal valid PDF content
MINIMAL_PDF_BYTES = b"""%PDF-1.4
1 0 obj
<< /Type /Catalog /Pages 2 0 R >>
endobj
2 0 obj
<< /Type /Pages /Kids [3 0 R] /Count 1 >>
endobj
3 0 obj
<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Contents 4 0 R >>
endobj
4 0 obj
<< /Length 20 >>
stream
BT /F1 12 Tf ET
endstream
endobj
xref
0 5
0000000000 65535 f 
0000000009 00000 n 
0000000058 00000 n 
0000000115 00000 n 
0000000202 00000 n 
0000000272 00000 n 
trailer
<< /Size 5 /Root 1 0 R >>
startxref
272
%%EOF
"""


def create_mock_pdfs(dms_dir: Path):
    dms_dir.mkdir(parents=True, exist_ok=True)
    for pdf_name in ["loan_agreement.pdf", "kfs.pdf", "sanction_letter.pdf", "disbursal_memo.pdf", "aadhaar.pdf", "pan.pdf"]:
        pdf_file = dms_dir / pdf_name
        if not pdf_file.exists():
            with open(pdf_file, "wb") as f:
                f.write(MINIMAL_PDF_BYTES)


def create_loan_001():
    loan_id = "LOAN_001"
    # LOS Data
    los_data = {
        "loan_id": loan_id,
        "applicant_name": "Rajesh Sharma",
        "loan_amount": 500000.0,
        "funding_amount": 500000.0,
        "applicant_mobile_no": "9876543210",
        "applicant_dob": "1990-05-15",
        "applicant_pan_number": "ABCDE1234F",
        "fathers_name": "Suresh Sharma",
        "applicant_bank_account_no": "987654321012",
        "loan_type": "Personal Loan",
        "loan_validity": "24 months",
        "current_address": "123 MG Road, Bengaluru, Karnataka, 560001",
        "permanent_address": "123 MG Road, Bengaluru, Karnataka, 560001",
        "aadhaar_no": "123456789012",
        "application_date": "2024-01-10",
        "bank_account_type": "Savings",
        "applicant_gender": "Male",
        "login_date": "2024-01-11",
        "disbursement_date": "2024-01-15",
        "tenure_months": 24,
        "application_id": "LOAN_001",
        "pan": "ABCDE1234F",
        "status": "APPROVED_FOR_DISBURSAL",
        "balance_transfer": 0,
    }
    with open(POC_DATA / f"los/loans/{loan_id}.json", "w") as f:
        json.dump(los_data, f, indent=2)

    # DMS
    dms_dir = POC_DATA / f"dms/{loan_id}"
    create_mock_pdfs(dms_dir)
    with open(dms_dir / "loan_agreement_otp_audit.json", "w") as f:
        json.dump({"otp_verified": True, "timestamp": "2026-08-30T10:15:00Z", "signer_id": "USER_98765"}, f, indent=2)
    with open(dms_dir / "aadhaar_xml_status.json", "w") as f:
        json.dump({"exists": True, "verified": True}, f, indent=2)

    # S3 Extracted
    ext_dir = POC_DATA / f"s3_extracted/{loan_id}"
    ext_dir.mkdir(parents=True, exist_ok=True)

    with open(ext_dir / "application_form.json", "w") as f:
        json.dump({
            "applicant_name": "Rajesh Sharma",
            "fathers_name": "Suresh Sharma",
            "dob": "1990-05-15",
            "mobile_no": "9876543210",
            "gender": "Male",
            "pan_number": "ABCDE1234F",
            "loan_amount": "500000",
            "loan_validity": "24 months",
            "account_no": "987654321012",
            "type_of_account": "Savings",
            "loan_type": "Personal Loan",
            "current_address": "123 MG Road, Bengaluru, Karnataka, 560001",
            "application_date": "2024-01-10",
            "application_no": "LOAN_001",
            "tenure_months": 24,
            "address_text": "123 MG Road, Bengaluru, Karnataka, 560001",
            "application_id": "LOAN_001",
        }, f, indent=2)

    with open(ext_dir / "aadhaar.json", "w") as f:
        json.dump({
            "applicant_name": "Rajesh Sharma",
            "address": "123 MG Road, Bengaluru, Karnataka, 560001",
            "aadhaar_number": "123456789012",
            "mobile_no": "9876543210",
            "dob": "1990-05-15",
        }, f, indent=2)

    with open(ext_dir / "pan.json", "w") as f:
        json.dump({
            "applicant_name": "Rajesh Sharma",
            "fathers_name": "Suresh Sharma",
            "pan_number": "ABCDE1234F",
            "dob": "1990-05-15",
        }, f, indent=2)

    with open(ext_dir / "account_statement.json", "w") as f:
        json.dump({
            "applicant_name": "Rajesh Sharma",
            "pan_number": "ABCDE1234F",
            "mobile_no": "9876543210",
            "account_no": "987654321012",
        }, f, indent=2)

    with open(ext_dir / "kyc_pan.json", "w") as f:
        json.dump({"pan_number": "ABCDE1234F", "name": "Rajesh Sharma"}, f, indent=2)

    with open(ext_dir / "kyc_address_proof.json", "w") as f:
        json.dump({"address_text": "123 MG Road, Bengaluru, Karnataka, 560001"}, f, indent=2)

    with open(ext_dir / "kfs.json", "w") as f:
        json.dump({
            "loan_amount": 500000.0,
            "funding_amount": 500000.0,
            "loan_validity": "24 months",
            "loan_type": "Personal Loan",
            "customer_consent": True,
            "bpi_charge": 1500.0,
            "broken_period_interest": 1500.0,
        }, f, indent=2)

    with open(ext_dir / "sanction_letter.json", "w") as f:
        json.dump({
            "applicant_name": "Rajesh Sharma",
            "loan_amount": 500000.0,
            "funding_amount": 500000.0,
            "tenure_months": 24,
            "broken_period_interest": 1500.0,
        }, f, indent=2)

    with open(ext_dir / "loan_agreement.json", "w") as f:
        json.dump({"loan_amount": 500000.0, "tenure_months": 24}, f, indent=2)

    with open(ext_dir / "disbursal_memo.json", "w") as f:
        json.dump({
            "loan_no": "LOAN_001",
            "loan_amount": 490000.0,
            "bpi_charge": 1500.0,
            "application_id": "LOAN_001",
            "closure_id": "CLOSURE_8877",
            "disbursal_amount": 490000.0,
        }, f, indent=2)

    v1 = [0.1] * 128
    v2 = [0.101] * 128
    with open(ext_dir / "face_embeddings.json", "w") as f:
        json.dump({"selfie_vector": v1, "application_form_photo_vector": v2}, f, indent=2)

    with open(ext_dir / "dms_status.json", "w") as f:
        json.dump({
            "aadhaar_xml": {"exists": True},
            "kfs": {"exists": True, "signed": True},
            "sanction_letter": {"exists": True},
            "loan_agreement": {"exists": True},
        }, f, indent=2)


def create_loan_002():
    loan_id = "LOAN_002"
    los_data = {
        "loan_id": loan_id,
        "applicant_name": "Priya Verma",
        "loan_amount": 750000.0,
        "funding_amount": 750000.0,
        "applicant_mobile_no": "9123456780",
        "applicant_dob": "1992-08-20",
        "applicant_pan_number": "XYZPK9988A",
        "fathers_name": "Anil Verma",
        "applicant_bank_account_no": "112233445566",
        "loan_type": "Home Loan",
        "loan_validity": "36 months",
        "current_address": "45 Park Street, Kolkata, West Bengal, 700016",
        "permanent_address": "45 Park Street, Kolkata, West Bengal, 700016",
        "aadhaar_no": "987654321098",
        "application_date": "2024-02-01",
        "bank_account_type": "Current",
        "applicant_gender": "Female",
        "login_date": "2024-02-02",
        "disbursement_date": "2024-02-10",
        "tenure_months": 36,
        "application_id": "LOAN_002",
        "pan": "XYZPK9988A",
        "status": "UNDER_REVIEW",
        "balance_transfer": 0,
    }
    with open(POC_DATA / f"los/loans/{loan_id}.json", "w") as f:
        json.dump(los_data, f, indent=2)

    dms_dir = POC_DATA / f"dms/{loan_id}"
    create_mock_pdfs(dms_dir)
    with open(dms_dir / "loan_agreement_otp_audit.json", "w") as f:
        json.dump({"otp_verified": False, "timestamp": "2026-08-30T14:20:00Z", "signer_id": "USER_11223"}, f, indent=2)
    with open(dms_dir / "aadhaar_xml_status.json", "w") as f:
        json.dump({"exists": False}, f, indent=2)

    ext_dir = POC_DATA / f"s3_extracted/{loan_id}"
    ext_dir.mkdir(parents=True, exist_ok=True)

    with open(ext_dir / "application_form.json", "w") as f:
        json.dump({
            "applicant_name": "Priya Verma",
            "fathers_name": "Anil Verma",
            "dob": "1992-08-20",
            "mobile_no": "9123456780",
            "gender": "Female",
            "pan_number": "XYZPK9988A",
            "loan_amount": "800000",
            "loan_validity": "36 months",
            "account_no": "112233445566",
            "type_of_account": "Current",
            "loan_type": "Home Loan",
            "current_address": "45 Park Street, Kolkata, West Bengal, 700016",
            "application_date": "2024-02-01",
            "application_no": "LOAN_002",
            "tenure_months": 36,
            "address_text": "45 Park Street, Kolkata, West Bengal, 700016",
            "application_id": "LOAN_002",
        }, f, indent=2)

    with open(ext_dir / "aadhaar.json", "w") as f:
        json.dump({
            "applicant_name": "Priya Verma",
            "address": "45 Park Street, Kolkata, West Bengal, 700016",
            "aadhaar_number": "987654321098",
            "mobile_no": "9123456780",
            "dob": "1992-08-20",
        }, f, indent=2)

    # Deliberate PAN number mismatch in PAN doc
    with open(ext_dir / "pan.json", "w") as f:
        json.dump({
            "applicant_name": "Priya Verma",
            "fathers_name": "Anil Verma",
            "pan_number": "WRONGPAN99",
            "dob": "1992-08-20",
        }, f, indent=2)

    with open(ext_dir / "account_statement.json", "w") as f:
        json.dump({
            "applicant_name": "Priya Verma",
            "pan_number": "XYZPK9988A",
            "mobile_no": "9123456780",
            "account_no": "112233445566",
        }, f, indent=2)

    with open(ext_dir / "kyc_pan.json", "w") as f:
        json.dump({"pan_number": "WRONGPAN99", "name": "Priya Verma"}, f, indent=2)

    with open(ext_dir / "kyc_address_proof.json", "w") as f:
        json.dump({"address_text": "45 Park Street, Kolkata, West Bengal, 700016"}, f, indent=2)

    # Deliberate loan_amount mismatch: 400000 vs 750000 (< 90%)
    with open(ext_dir / "kfs.json", "w") as f:
        json.dump({
            "loan_amount": 400000.0,
            "funding_amount": 400000.0,
            "loan_validity": "36 months",
            "loan_type": "Home Loan",
            "customer_consent": True,
            "bpi_charge": 2000.0,
            "broken_period_interest": 2000.0,
        }, f, indent=2)

    with open(ext_dir / "sanction_letter.json", "w") as f:
        json.dump({
            "applicant_name": "Priya Verma",
            "loan_amount": 400000.0,
            "funding_amount": 400000.0,
            "tenure_months": 36,
            "broken_period_interest": 2000.0,
        }, f, indent=2)

    with open(ext_dir / "loan_agreement.json", "w") as f:
        json.dump({"loan_amount": 750000.0, "tenure_months": 36}, f, indent=2)

    with open(ext_dir / "disbursal_memo.json", "w") as f:
        json.dump({
            "loan_no": "LOAN_002",
            "loan_amount": 400000.0,
            "bpi_charge": 2000.0,
            "application_id": "LOAN_002",
            "closure_id": "CLOSURE_9900",
            "disbursal_amount": 400000.0,
        }, f, indent=2)

    v1 = [0.9] * 128
    v2 = [-0.9] * 128
    with open(ext_dir / "face_embeddings.json", "w") as f:
        json.dump({"selfie_vector": v1, "application_form_photo_vector": v2}, f, indent=2)

    with open(ext_dir / "dms_status.json", "w") as f:
        json.dump({
            "aadhaar_xml": {"exists": False},
            "kfs": {"exists": True},
            "sanction_letter": {"exists": True},
            "loan_agreement": {"exists": True},
        }, f, indent=2)


def create_loan_003():
    loan_id = "LOAN_003"
    # LOS Data - "Mohammad Rizwan"
    los_data = {
        "loan_id": loan_id,
        "applicant_name": "Mohammad Rizwan",
        "loan_amount": 300000.0,
        "funding_amount": 300000.0,
        "applicant_mobile_no": "9988776655",
        "applicant_dob": "1988-11-25",
        "applicant_pan_number": "MNOPQ5544Z",
        "fathers_name": "Abdul Rizwan",
        "applicant_bank_account_no": "556677889900",
        "loan_type": "Two Wheeler Loan",
        "loan_validity": "12 months",
        "current_address": "Flat 204, Green Heights, Lucknow, Uttar Pradesh, 226001",
        "permanent_address": "Flat 204, Green Heights, Lucknow, Uttar Pradesh, 226001",
        "aadhaar_no": "112233445566",
        "application_date": "2024-03-05",
        "bank_account_type": "Savings",
        "applicant_gender": "Male",
        "login_date": "2024-03-06",
        "disbursement_date": "2024-03-12",
        "tenure_months": 12,
        "application_id": "LOAN_003",
        "pan": "MNOPQ5544Z",
        "status": "APPROVED",
        "balance_transfer": 0,
    }
    with open(POC_DATA / f"los/loans/{loan_id}.json", "w") as f:
        json.dump(los_data, f, indent=2)

    dms_dir = POC_DATA / f"dms/{loan_id}"
    create_mock_pdfs(dms_dir)
    with open(dms_dir / "loan_agreement_otp_audit.json", "w") as f:
        json.dump({"otp_verified": True, "timestamp": "2026-08-31T09:00:00Z", "signer_id": "USER_55441"}, f, indent=2)
    with open(dms_dir / "aadhaar_xml_status.json", "w") as f:
        json.dump({"exists": True, "verified": True}, f, indent=2)

    ext_dir = POC_DATA / f"s3_extracted/{loan_id}"
    ext_dir.mkdir(parents=True, exist_ok=True)

    # Name is "Mohd Rizwan" -> triggers PARTIAL & LLM adjudication
    with open(ext_dir / "application_form.json", "w") as f:
        json.dump({
            "applicant_name": "Mohd Rizwan",
            "fathers_name": "Abdul Rizwan",
            "dob": "1988-11-25",
            "mobile_no": "9988776655",
            "gender": "Male",
            "pan_number": "MNOPQ5544Z",
            "loan_amount": "300000",
            "loan_validity": "12 months",
            "account_no": "556677889900",
            "type_of_account": "Savings",
            "loan_type": "Two Wheeler Loan",
            "current_address": "Flat 204, Green Heights, Lucknow, Uttar Pradesh, 226001",
            "application_date": "2024-03-05",
            "application_no": "LOAN_003",
            "tenure_months": 12,
            "address_text": "Flat 204, Green Heights, Lucknow, Uttar Pradesh, 226001",
            "application_id": "LOAN_003",
        }, f, indent=2)

    with open(ext_dir / "aadhaar.json", "w") as f:
        json.dump({
            "applicant_name": "Mohammad Rizwan",
            "address": "Flat 204, Green Heights, Lucknow, Uttar Pradesh, 226001",
            "aadhaar_number": "112233445566",
            "mobile_no": "9988776655",
            "dob": "1988-11-25",
        }, f, indent=2)

    with open(ext_dir / "pan.json", "w") as f:
        json.dump({
            "applicant_name": "Mohd Rizwan",
            "fathers_name": "Abdul Rizwan",
            "pan_number": "MNOPQ5544Z",
            "dob": "1988-11-25",
        }, f, indent=2)

    with open(ext_dir / "account_statement.json", "w") as f:
        json.dump({
            "applicant_name": "Mohammad Rizwan",
            "pan_number": "MNOPQ5544Z",
            "mobile_no": "9988776655",
            "account_no": "556677889900",
        }, f, indent=2)

    with open(ext_dir / "kyc_pan.json", "w") as f:
        json.dump({"pan_number": "MNOPQ5544Z", "name": "Mohd Rizwan"}, f, indent=2)

    with open(ext_dir / "kyc_address_proof.json", "w") as f:
        json.dump({"address_text": "Flat 204, Green Heights, Lucknow, Uttar Pradesh, 226001"}, f, indent=2)

    with open(ext_dir / "kfs.json", "w") as f:
        json.dump({
            "loan_amount": 300000.0,
            "funding_amount": 300000.0,
            "loan_validity": "12 months",
            "loan_type": "Two Wheeler Loan",
            "customer_consent": True,
            "bpi_charge": 800.0,
            "broken_period_interest": 800.0,
        }, f, indent=2)

    with open(ext_dir / "sanction_letter.json", "w") as f:
        json.dump({
            "applicant_name": "Mohammad Rizwan",
            "loan_amount": 300000.0,
            "funding_amount": 300000.0,
            "tenure_months": 12,
            "broken_period_interest": 800.0,
        }, f, indent=2)

    with open(ext_dir / "loan_agreement.json", "w") as f:
        json.dump({"loan_amount": 300000.0, "tenure_months": 12}, f, indent=2)

    with open(ext_dir / "disbursal_memo.json", "w") as f:
        json.dump({
            "loan_no": "LOAN_003",
            "loan_amount": 285000.0,
            "bpi_charge": 800.0,
            "application_id": "LOAN_003",
            "closure_id": "CLOSURE_3322",
            "disbursal_amount": 285000.0,
        }, f, indent=2)

    v1 = [0.5] * 128
    v2 = [0.501] * 128
    with open(ext_dir / "face_embeddings.json", "w") as f:
        json.dump({"selfie_vector": v1, "application_form_photo_vector": v2}, f, indent=2)

    with open(ext_dir / "dms_status.json", "w") as f:
        json.dump({
            "aadhaar_xml": {"exists": True},
            "kfs": {"exists": True, "signed": True},
            "sanction_letter": {"exists": True},
            "loan_agreement": {"exists": True},
        }, f, indent=2)


if __name__ == "__main__":
    import shutil
    create_loan_001()
    create_loan_002()
    create_loan_003()

    # Mirror to s3_los and s3_extracted_structured
    s3_los_dir = POC_DATA / "s3_los"
    s3_los_dir.mkdir(parents=True, exist_ok=True)
    for lf in (POC_DATA / "los/loans").glob("*.json"):
        shutil.copy2(lf, s3_los_dir / lf.name)

    s3_struct_dir = POC_DATA / "s3_extracted_structured"
    s3_struct_dir.mkdir(parents=True, exist_ok=True)
    for ext_case_dir in (POC_DATA / "s3_extracted").iterdir():
        if ext_case_dir.is_dir():
            target_case_dir = s3_struct_dir / ext_case_dir.name
            target_case_dir.mkdir(parents=True, exist_ok=True)
            for jf in ext_case_dir.glob("*.json"):
                shutil.copy2(jf, target_case_dir / jf.name)

    # Populate s3_result with baseline verification reports and scorecards
    from pipeline.storage import get_s3_los, get_all_s3_extracted_structured
    from pipeline.nodes.check_kyc import check_kyc
    from pipeline.nodes.check_financial import check_financial
    from pipeline.nodes.check_loan_app import check_loan_app as check_loan_application
    from pipeline.nodes.compile_report import compile_report
    from pipeline.nodes.generate_scorecard import generate_scorecard
    from pipeline.nodes.push_results import push_results

    for loan_id in ["LOAN_001", "LOAN_002", "LOAN_003"]:
        los_data = get_s3_los(loan_id) or {}
        struct_data = get_all_s3_extracted_structured(loan_id)
        state = {
            "loan_id": loan_id,
            "application_id": los_data.get("application_id", loan_id),
            "los_data": los_data,
            "raw_doc_paths": {},
            "extracted_data": struct_data,
            "extracted_structured_data": struct_data,
            "face_embeddings": struct_data.get("face_embeddings", {}),
            "dms_status": struct_data.get("dms_status", {}),
            "otp_audit": {"otp_verified": True} if loan_id != "LOAN_002" else {"otp_verified": False},
            "kyc_comparison_results": [],
            "financial_comparison_results": [],
            "loan_app_comparison_results": [],
            "comparison_results": [],
            "subnode_rollups": {},
            "scorecard": {},
            "audit_log": [],
            "errors": [],
            "node_history": ["fetch_los", "fetch_dms", "idp_scan", "llm_structure"],
        }
        s1 = check_kyc(state)
        s2 = check_financial(state)
        s3 = check_loan_application(state)
        state["kyc_comparison_results"] = s1["records"]
        state["financial_comparison_results"] = s2["records"]
        state["loan_app_comparison_results"] = s3["records"]
        state["kyc_rollup"] = s1["rollup"]
        state["financial_rollup"] = s2["rollup"]
        state["loan_app_rollup"] = s3["rollup"]
        state = compile_report(state)
        state = generate_scorecard(state)
        state = push_results(state)

    print("Mock data and s3_results generated successfully for LOAN_001, LOAN_002, LOAN_003 across all storage tiers")


