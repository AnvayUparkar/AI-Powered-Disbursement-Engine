"""Unit and integration tests for Aadhaar XML UID extraction and masked comparison."""
import pytest
from pathlib import Path
from typing import Any

from idp.models.document import DocumentSource, PageInformation, ParsedDocument, ProcessingMetadata
from idp.services.output.serializer import OutputSerializer
from idp.services.output.canonical_builder import build_canonical_extracted_dict as build_idp_result_from_parsed
from pipeline.nodes.check_kyc import check_kyc

from pipeline.state import PipelineState
from app.serializers.case_context import CaseContext
from app.serializers.checkpoints.identity_kyc import build_aadhaar_xml_checkpoint


def test_parse_xml_fast_path_extracts_uid(tmp_path: Path):
    """UID attribute on <UidData> is extracted into custom_metadata['aadhaar_uid']."""
    xml_content = """<?xml version="1.0" encoding="UTF-8"?>
<OfflinePaperlessKyc>
  <UidData tkn="token123" uid="xxxxxxxx1407">
    <Poi dob="21-10-1996" gender="M" name="Sharath Kumar S"/>
    <Poa co="S/O" dist="Bengaluru" state="Karnataka"/>
  </UidData>
</OfflinePaperlessKyc>"""
    xml_file = tmp_path / "test_aadhaar.xml"
    xml_file.write_text(xml_content, encoding="utf-8")

    serializer = OutputSerializer()
    parsed = serializer.parse_xml_fast_path(str(xml_file), doc_id="doc_test_xml")

    assert parsed is not None
    assert parsed.custom_metadata is not None
    assert parsed.custom_metadata.get("aadhaar_uid") == "xxxxxxxx1407"


def test_build_idp_result_from_parsed_injects_uid():
    """build_idp_result_from_parsed injects aadhaar_number from custom_metadata."""
    parsed = ParsedDocument(
        document_id="doc_xml_001",
        source=DocumentSource(filename="test.xml", mime_type="application/xml"),
        pages=[PageInformation(page_number=1, width=800.0, height=1100.0, elements=[])],
        elements=[],
        text="Sharath Kumar S\n21-10-1996",
        processing=ProcessingMetadata(
            document_id="doc_xml_001",
            processing_id="proc-1",
            file_type="xml",
            mime_type="application/xml",
            file_size_bytes=100,
            page_count=1,
        ),
        custom_metadata={"aadhaar_uid": "xxxxxxxx1407"},
    )

    res = build_idp_result_from_parsed(parsed=parsed, doc_type="aadhaar_xml", doc_id="doc_xml_001")
    assert res.get("aadhaar_number") == "xxxxxxxx1407"


def test_check_kyc_aadhaar_xml_masked_comparison_match():
    """check_kyc runs masked Aadhaar comparison between XML uid and LOS aadhaar_no (happy path MATCH)."""
    state: PipelineState = {
        "loan_id": "APPL00327707",
        "raw_doc_paths": {},
        "extracted_data": {
            "aadhaar_xml": {
                "aadhaar_xml_present": True,
                "aadhaar_number": "xxxxxxxx1407",
            }
        },
        "los_data": {
            "loan_id": "APPL00327707",
            "aadhaar_no": "XXXXXXXX 1407",
            "applicant_name": "Sharath Kumar S",
        },
    }

    res = check_kyc(state)
    records = res["records"]

    uid_rec = next(
        (r for r in records if r.get("check_id") in (
            "chk_aadhaar_xml_aadhaar_no_vs_los",
            "chk_check_kyc_aadhaar_xml_aadhaar_number_vs_los",
        )),
        None,
    )
    assert uid_rec is not None, "Aadhaar XML UID check record was not found"
    assert uid_rec["match_status"] == "MATCH"
    assert uid_rec["confidence"] == 1.0
    assert uid_rec["sources"] == ["aadhaar_xml", "los"]
    assert uid_rec["values"] == ["xxxxxxxx1407", "XXXXXXXX 1407"]


def test_check_kyc_aadhaar_xml_masked_comparison_mismatch():
    """check_kyc reports MISMATCH when XML UID last-4 digits differ from LOS."""
    state: PipelineState = {
        "loan_id": "APPL00327707",
        "raw_doc_paths": {},
        "extracted_data": {
            "aadhaar_xml": {
                "aadhaar_xml_present": True,
                "aadhaar_number": "xxxxxxxx9999",
            }
        },
        "los_data": {
            "loan_id": "APPL00327707",
            "aadhaar_no": "XXXXXXXX 1407",
            "applicant_name": "Sharath Kumar S",
        },
    }

    res = check_kyc(state)
    records = res["records"]

    uid_rec = next(
        (r for r in records if r.get("check_id") in (
            "chk_aadhaar_xml_aadhaar_no_vs_los",
            "chk_check_kyc_aadhaar_xml_aadhaar_number_vs_los",
        )),
        None,
    )
    assert uid_rec is not None
    assert uid_rec["match_status"] == "MISMATCH"
    assert uid_rec["confidence"] == 0.0


def test_check_kyc_aadhaar_xml_missing_doc_optional_no_failure():
    """When aadhaar_xml is not in extracted_data, it is treated as optional and does not emit failure."""
    state: PipelineState = {
        "loan_id": "APPL_NO_XML",
        "raw_doc_paths": {},
        "extracted_data": {
            "aadhaar": {
                "applicant_name": "John Doe",
                "aadhaar_number": "XXXXXXXX1234",
            }
        },
        "los_data": {
            "loan_id": "APPL_NO_XML",
            "aadhaar_no": "XXXXXXXX1234",
            "applicant_name": "John Doe",
        },
    }

    res = check_kyc(state)
    # Ensure no NOT_FOUND record was generated for aadhaar_xml
    xml_recs = [r for r in res["records"] if "aadhaar_xml" in r.get("sources", [])]
    assert len(xml_recs) == 0


def test_build_aadhaar_xml_checkpoint_surfaces_uid_and_comparison(tmp_path: Path):
    """CP 9 checkpoint surfaces both presence and Aadhaar Number fields & comparisons."""
    uid_record = {
        "check_id": "chk_aadhaar_xml_aadhaar_no_vs_los",
        "field": "aadhaar_number",
        "sources": ["aadhaar_xml", "los"],
        "values": ["xxxxxxxx1407", "XXXXXXXX 1407"],
        "match_type": "masked_id",
        "match_status": "MATCH",
        "confidence": 1.0,
        "method": "masked_aadhaar_compare",
    }
    presence_record = {
        "check_id": "chk_aadhaar_xml_mandatory_presence",
        "field": "aadhaar_xml_present",
        "sources": ["aadhaar_xml"],
        "values": [True],
        "match_type": "presence",
        "match_status": "MATCH",
        "confidence": 1.0,
        "method": "presence_only",
    }

    ctx = CaseContext(
        loan_id="APPL00327707",
        los_data={"loan_id": "APPL00327707", "aadhaar_no": "XXXXXXXX 1407"},
        docs={
            "aadhaar_xml": {
                "aadhaar_xml_present": True,
                "aadhaar_number": "xxxxxxxx1407",
            }
        },
        real_doc_names=["Aadhaar Xml.xml"],
        doc_ids=["doc-APPL00327707-aadhaarxml"],
        records=[presence_record, uid_record],
        records_by_id={
            "chk_aadhaar_xml_mandatory_presence": presence_record,
            "chk_aadhaar_xml_aadhaar_no_vs_los": uid_record,
        },
        records_by_field={"aadhaar_number": [uid_record]},
        records_by_subnode={"check_kyc": [uid_record]},
        status_data={"status": "DONE"},
        scorecard_data={},
        subnode_rollups={},
        loan_amount=500000.0,
        disbursal_amount=450000.0,
        applicant_name="Sharath Kumar S",
        app_id="APP-APPL00327707",
        loan_type="Personal Loan",
        is_bt=False,
        raw_dir=tmp_path / "raw",
        dms_dir=tmp_path / "dms",
        extracted_dir=tmp_path / "extracted",
        extracted_structured_dir=tmp_path / "extracted_structured",
        result_dir=tmp_path / "result",
    )

    cp = build_aadhaar_xml_checkpoint(ctx)
    assert cp["id"] == 9
    assert cp["status"] == "VERIFIED"
    assert cp["confidence"] == 100.0

    # Verify extractedFields has both Presence and Aadhaar Number
    field_map = {f["name"]: f["value"] for f in cp["extractedFields"]}
    assert "Aadhaar XML Presence" in field_map
    assert field_map["Aadhaar XML Presence"] == "Present"
    assert "Aadhaar Number" in field_map
    assert field_map["Aadhaar Number"] == "xxxxxxxx1407"

    # Verify comparison records are attached
    comp_ids = [c["check_id"] for c in cp["comparisons"]]
    assert "chk_aadhaar_xml_aadhaar_no_vs_los" in comp_ids


def test_aadhaar_xml_zero_llm_calls(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Verifies that Aadhaar XML completely bypasses LLM extraction across parsing and structuring."""
    from unittest.mock import MagicMock
    from pipeline.nodes.llm_structure import _structure_single_document

    mock_llm = MagicMock(side_effect=RuntimeError("LLM should not be called for Aadhaar XML"))
    monkeypatch.setattr("pipeline.engines.llm_field_extractor.llm_extract_fields", mock_llm)
    monkeypatch.setattr("pipeline.nodes.llm_structure.llm_extract_fields", mock_llm)


    xml_content = """<?xml version="1.0" encoding="UTF-8"?>
<Certificate>
  <CertificateData>
    <KycRes>
      <UidData uid="xxxxxxxx3054">
        <Poi dob="27-05-2001" gender="F" name="Shital Bhusariya"/>
      </UidData>
    </KycRes>
  </CertificateData>
</Certificate>"""
    xml_file = tmp_path / "Aadhaar XML.xml"
    xml_file.write_text(xml_content, encoding="utf-8")

    serializer = OutputSerializer()
    parsed = serializer.parse_xml_fast_path(str(xml_file), doc_id="APPL00388856_aadhaar_xml")

    assert parsed.custom_metadata.get("aadhaar_uid") == "xxxxxxxx3054"
    assert parsed.custom_metadata.get("aadhaar_xml_present") is True

    # 1. Build IDP scan result (must not call mock_llm)
    idp_res = build_idp_result_from_parsed(parsed=parsed, doc_type="aadhaar_xml", doc_id="APPL00388856_aadhaar_xml")
    assert idp_res.get("aadhaar_number") == "xxxxxxxx3054"
    assert idp_res.get("aadhaar_xml_present") is True

    # 2. Node 4 structure document (must not call mock_llm)
    struct_res = _structure_single_document("aadhaar_xml", idp_res, "APPL00388856")
    assert struct_res.get("aadhaar_number") == "xxxxxxxx3054"
    assert struct_res.get("aadhaar_xml_present") is True

    # Confirm LLM was never called
    mock_llm.assert_not_called()
