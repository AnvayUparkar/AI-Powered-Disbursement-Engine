import json
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
POC_DATA = BASE_DIR / "poc_data"

# Ensure directories
for folder in ["los/loans", "los/scorecards_received", "dms", "s3_raw", "s3_extracted", "s3_result"]:
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
trailer
<< /Size 5 /Root 1 0 R >>
startxref
272
%%EOF
"""

def create_mock_pdfs(dms_dir: Path):
    dms_dir.mkdir(parents=True, exist_ok=True)
    for pdf_name in ["loan_agreement.pdf", "kfs.pdf", "sanction_letter.pdf", "disbursal_memo.pdf"]:
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
        "funding_amount": 500000.0,
        "tenure_months": 24,
        "application_id": "APP_001",
        "pan": "ABCDE1234F",
        "status": "APPROVED_FOR_DISBURSAL"
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
    with open(dms_dir / "kfs.metadata.json", "w") as f:
        json.dump({"exists": True, "signed": True}, f, indent=2)
    with open(dms_dir / "sanction_letter.metadata.json", "w") as f:
        json.dump({"exists": True}, f, indent=2)
    with open(dms_dir / "disbursal_memo.metadata.json", "w") as f:
        json.dump({"exists": True}, f, indent=2)

    # S3 Extracted
    ext_dir = POC_DATA / f"s3_extracted/{loan_id}"
    ext_dir.mkdir(parents=True, exist_ok=True)

    with open(ext_dir / "application_form.json", "w") as f:
        json.dump({
            "applicant_name": "Rajesh Sharma",
            "loan_amount": "500000",
            "tenure_months": 24,
            "pan_number": "ABCDE1234F",
            "address_text": "123 MG Road, Bengaluru, Karnataka, 560001",
            "application_id": "APP_001"
        }, f, indent=2)

    with open(ext_dir / "kyc_pan.json", "w") as f:
        json.dump({"pan_number": "ABCDE1234F", "name": "Rajesh Sharma"}, f, indent=2)

    with open(ext_dir / "kyc_address_proof.json", "w") as f:
        json.dump({"address_text": "123 MG Road, Bengaluru, Karnataka, 560001"}, f, indent=2)

    with open(ext_dir / "aadhar_xml.json", "w") as f:
        json.dump({"name": "Rajesh Sharma", "address": "123 MG Road, Bengaluru, Karnataka, 560001"}, f, indent=2)

    with open(ext_dir / "kfs.json", "w") as f:
        json.dump({"loan_amount": 500000.0, "funding_amount": 500000.0, "broken_period_interest": 1500.0}, f, indent=2)

    with open(ext_dir / "sanction_letter.json", "w") as f:
        json.dump({"loan_amount": 500000.0, "funding_amount": 500000.0, "tenure_months": 24, "broken_period_interest": 1500.0}, f, indent=2)

    with open(ext_dir / "loan_agreement.json", "w") as f:
        json.dump({"loan_amount": 500000.0, "tenure_months": 24}, f, indent=2)

    with open(ext_dir / "disbursal_memo.json", "w") as f:
        json.dump({
            "application_id": "APP_001",
            "closure_id": "CLOSURE_8877",
            "disbursal_amount": 480000.0
        }, f, indent=2)

    # Face embeddings with high similarity
    v1 = [0.1] * 128
    v2 = [0.101] * 128
    with open(ext_dir / "face_embeddings.json", "w") as f:
        json.dump({"selfie_vector": v1, "application_form_photo_vector": v2}, f, indent=2)

    with open(ext_dir / "dms_status.json", "w") as f:
        json.dump({
            "aadhaar_xml": {"exists": True},
            "kfs": {"exists": True, "signed": True},
            "sanction_letter": {"exists": True},
            "loan_agreement": {"exists": True}
        }, f, indent=2)


def create_loan_002():
    loan_id = "LOAN_002"
    # LOS Data - Mismatches
    los_data = {
        "loan_id": loan_id,
        "applicant_name": "Priya Verma",
        "funding_amount": 750000.0,
        "tenure_months": 36,
        "application_id": "APP_002",
        "pan": "XYZPK9988A",
        "status": "UNDER_REVIEW"
    }
    with open(POC_DATA / f"los/loans/{loan_id}.json", "w") as f:
        json.dump(los_data, f, indent=2)

    # DMS
    dms_dir = POC_DATA / f"dms/{loan_id}"
    create_mock_pdfs(dms_dir)
    with open(dms_dir / "loan_agreement_otp_audit.json", "w") as f:
        json.dump({"otp_verified": False, "timestamp": "2026-08-30T14:20:00Z", "signer_id": "USER_11223"}, f, indent=2)
    # Aadhaar XML MISSING (Hard Gate)
    with open(dms_dir / "aadhaar_xml_status.json", "w") as f:
        json.dump({"exists": False}, f, indent=2)

    # S3 Extracted - Intentional discrepancies
    ext_dir = POC_DATA / f"s3_extracted/{loan_id}"
    ext_dir.mkdir(parents=True, exist_ok=True)

    with open(ext_dir / "application_form.json", "w") as f:
        json.dump({
            "applicant_name": "Priya Verma",
            "loan_amount": "800000",  # Mismatch with LOS 750000
            "tenure_months": 36,
            "pan_number": "XYZPK9988A",
            "address_text": "45 Park Street, Kolkata, West Bengal, 700016",
            "application_id": "APP_002"
        }, f, indent=2)

    with open(ext_dir / "kyc_pan.json", "w") as f:
        json.dump({"pan_number": "XYZPK9988A", "name": "Priya Verma"}, f, indent=2)

    with open(ext_dir / "kyc_address_proof.json", "w") as f:
        json.dump({"address_text": "45 Park Street, Kolkata, West Bengal, 700016"}, f, indent=2)

    with open(ext_dir / "kfs.json", "w") as f:
        json.dump({"loan_amount": 750000.0, "funding_amount": 750000.0, "broken_period_interest": 2000.0}, f, indent=2)

    with open(ext_dir / "sanction_letter.json", "w") as f:
        json.dump({"loan_amount": 700000.0, "funding_amount": 700000.0, "tenure_months": 36, "broken_period_interest": 2000.0}, f, indent=2)

    with open(ext_dir / "loan_agreement.json", "w") as f:
        json.dump({"loan_amount": 750000.0, "tenure_months": 36}, f, indent=2)

    with open(ext_dir / "disbursal_memo.json", "w") as f:
        json.dump({
            "application_id": "APP_002",
            "closure_id": "CLOSURE_9900",
            "disbursal_amount": 400000.0  # < 90% of 750000 (which is 675000)
        }, f, indent=2)

    # Face embeddings with mismatch
    v1 = [0.9] * 128
    v2 = [-0.9] * 128
    with open(ext_dir / "face_embeddings.json", "w") as f:
        json.dump({"selfie_vector": v1, "application_form_photo_vector": v2}, f, indent=2)

    with open(ext_dir / "dms_status.json", "w") as f:
        json.dump({
            "aadhaar_xml": {"exists": False},
            "kfs": {"exists": True},
            "sanction_letter": {"exists": True},
            "loan_agreement": {"exists": True}
        }, f, indent=2)


def create_loan_003():
    loan_id = "LOAN_003"
    # LOS Data - Partial name match variant
    los_data = {
        "loan_id": loan_id,
        "applicant_name": "Mohammad Rizwan",
        "funding_amount": 300000.0,
        "tenure_months": 12,
        "application_id": "APP_003",
        "pan": "MNOPQ5544Z",
        "status": "APPROVED"
    }
    with open(POC_DATA / f"los/loans/{loan_id}.json", "w") as f:
        json.dump(los_data, f, indent=2)

    # DMS
    dms_dir = POC_DATA / f"dms/{loan_id}"
    create_mock_pdfs(dms_dir)
    with open(dms_dir / "loan_agreement_otp_audit.json", "w") as f:
        json.dump({"otp_verified": True, "timestamp": "2026-08-31T09:00:00Z", "signer_id": "USER_55441"}, f, indent=2)
    with open(dms_dir / "aadhaar_xml_status.json", "w") as f:
        json.dump({"exists": True, "verified": True}, f, indent=2)

    # S3 Extracted - "Mohd Rizwan" vs "Mohammad Rizwan"
    ext_dir = POC_DATA / f"s3_extracted/{loan_id}"
    ext_dir.mkdir(parents=True, exist_ok=True)

    with open(ext_dir / "application_form.json", "w") as f:
        json.dump({
            "applicant_name": "Mohd Rizwan",  # Jaro-Winkler with Mohammad Rizwan is ~0.87 (PARTIAL band)
            "loan_amount": "300000",
            "tenure_months": 12,
            "pan_number": "MNOPQ5544Z",
            "address_text": "Flat 204, Green Heights, Lucknow, Uttar Pradesh, 226001",
            "application_id": "APP_003"
        }, f, indent=2)

    with open(ext_dir / "kyc_pan.json", "w") as f:
        json.dump({"pan_number": "MNOPQ5544Z", "name": "Mohd Rizwan"}, f, indent=2)

    with open(ext_dir / "kyc_address_proof.json", "w") as f:
        json.dump({"address_text": "Flat 204, Green Heights, Lucknow, Uttar Pradesh, 226001"}, f, indent=2)

    with open(ext_dir / "aadhar_xml.json", "w") as f:
        json.dump({"name": "Mohammad Rizwan", "address": "Flat 204, Green Heights, Lucknow, Uttar Pradesh, 226001"}, f, indent=2)

    with open(ext_dir / "kfs.json", "w") as f:
        json.dump({"loan_amount": 300000.0, "funding_amount": 300000.0, "broken_period_interest": 800.0}, f, indent=2)

    with open(ext_dir / "sanction_letter.json", "w") as f:
        json.dump({"loan_amount": 300000.0, "funding_amount": 300000.0, "tenure_months": 12, "broken_period_interest": 800.0}, f, indent=2)

    with open(ext_dir / "loan_agreement.json", "w") as f:
        json.dump({"loan_amount": 300000.0, "tenure_months": 12}, f, indent=2)

    with open(ext_dir / "disbursal_memo.json", "w") as f:
        json.dump({
            "application_id": "APP_003",
            "closure_id": "CLOSURE_3322",
            "disbursal_amount": 285000.0
        }, f, indent=2)

    # Face embeddings with match
    v1 = [0.5] * 128
    v2 = [0.501] * 128
    with open(ext_dir / "face_embeddings.json", "w") as f:
        json.dump({"selfie_vector": v1, "application_form_photo_vector": v2}, f, indent=2)

    with open(ext_dir / "dms_status.json", "w") as f:
        json.dump({
            "aadhaar_xml": {"exists": True},
            "kfs": {"exists": True, "signed": True},
            "sanction_letter": {"exists": True},
            "loan_agreement": {"exists": True}
        }, f, indent=2)


if __name__ == "__main__":
    create_loan_001()
    create_loan_002()
    create_loan_003()
    print("Mock data generated successfully for LOAN_001, LOAN_002, LOAN_003")
