"""Tests for per-document-type templates (pipeline/engines/doc_templates)."""
import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from config import pipeline_checks
from config.doc_types import DOC_TYPE_ALIASES
from pipeline.engines import doc_templates
from pipeline.engines.doc_templates import (
    TEMPLATE_DIR,
    build_system_prompt,
    build_ui_json,
    format_ui_json_text,
    get_doc_template,
    load_template_file,
    ui_label_for,
)
from pipeline.engines.llm_field_extractor import format_template_json, llm_extract_fields
from pipeline.engines.prompts import get_system_prompt

SANCTION_UI_LABELS = [
    "Name",
    "Date",
    "Address",
    "Ref. Number: LOS ID",
    "Sanction Number",
    "Contact Number",
    "Loan Amount (Rs)",
    "Annual Percentage Rate (APR) (%)",
    "Tenor (Months)",
    "EMI Payment Date",
]

KFS_UI_LABELS = [
    "Applicant Name",
    "Date",
    "Applicant No",
    "Loan Proposal / Account No",
    "Type of Loan",
    "Sanctioned Loan Amount (INR)",
    "Loan Term (Months)",
    "No. of EPIs",
    "Repayment Commencement",
    "Interest Rate (%)",
    "Interest Type",
    "APR (%)",
    "Total Interest (INR)",
    "Net Disbursed Amount (INR)",
    "Total Amount Payable (INR)",
    "Customer Consent",
]

APP_FORM_UI_LABELS = [
    "Application Date",
    "LOS No",
    "Applicant Name",
    "PAN",
    "Date of Birth",
    "Mobile",
    "Email ID",
    "Aadhaar No",
    "CKYC No",
    "Present Residence",
    "Present Address",
    "Permanent Residence",
    "Permanent Address",
    "Co-Applicant 1 Present Residence",
    "Co-Applicant 1 Present Address",
    "Co-Applicant 2 Present Residence",
    "Co-Applicant 2 Present Address",
    "Employer Name",
    "Office Address",
    "Account Number",
    "Loan Amount (Rs)",
    "Tenure",
    "Reference 1",
    "Reference 2",
    "Customer Photo",
    "HDB Contact Person",
]

UI_LABELS_BY_TYPE = {
    "sanction_letter": SANCTION_UI_LABELS,
    "kfs": KFS_UI_LABELS,
    "application_form": APP_FORM_UI_LABELS,
}

TEMPLATED_TYPES = sorted(UI_LABELS_BY_TYPE)

DISPLAY_NAMES = {
    "sanction_letter": "Sanction Letter",
    "kfs": "Key Fact Statement (KFS)",
    "application_form": "Application Form",
}


EMPTY_REFERENCE = {"Name": None, "Relationship": None, "Residential Address": None}
NESTED_UI_LABELS = {"Reference 1", "Reference 2"}


def _empty_ui_json(doc_type):
    return {
        "document_type": DISPLAY_NAMES[doc_type],
        **{label: (EMPTY_REFERENCE if label in NESTED_UI_LABELS else None) for label in UI_LABELS_BY_TYPE[doc_type]},
    }


def _write_template(tmp_path: Path, name: str, body: str) -> Path:
    path = tmp_path / f"{name}.yaml"
    path.write_text(body, encoding="utf-8")
    return path


# ── Every shipped template is valid ───────────────────────────────────────

@pytest.mark.parametrize("path", sorted(TEMPLATE_DIR.glob("*.yaml")), ids=lambda p: p.name)
def test_every_shipped_template_loads_and_is_a_known_doc_type(path):
    template = load_template_file(path)
    assert template.doc_type in DOC_TYPE_ALIASES
    assert template.ui_fields, "a template must show at least one field on the UI"


def test_every_shipped_type_is_covered_by_these_tests():
    assert sorted(p.stem for p in TEMPLATE_DIR.glob("*.yaml")) == TEMPLATED_TYPES


# ── Templates match the requested sheets and keep what the checks need ────

@pytest.mark.parametrize("doc_type", TEMPLATED_TYPES)
def test_ui_labels_match_the_requested_sheet_in_order(doc_type):
    template = get_doc_template(doc_type)
    assert [f.label for f in template.ui_fields] == UI_LABELS_BY_TYPE[doc_type]


@pytest.mark.parametrize("doc_type", TEMPLATED_TYPES)
def test_template_keeps_every_key_the_los_checks_read(doc_type):
    template_keys = {f.key for f in get_doc_template(doc_type).fields}
    checks = [
        check
        for table in (
            pipeline_checks.FINANCIAL_FIELD_CHECKS,
            pipeline_checks.KYC_FIELD_CHECKS,
            pipeline_checks.LOAN_APP_FIELD_CHECKS,
        )
        for check in table.get(doc_type, [])
    ]
    assert checks, f"expected LOS checks to be defined for {doc_type}"
    # A check finds its value under doc_field or any alias (e.g. KFS 'bpi' is stored as 'BPI').
    missing = [c["doc_field"] for c in checks if not ({c["doc_field"], *c.get("aliases", [])} & template_keys)]
    assert not missing, f"{doc_type} template drops keys the LOS checks read: {missing}"


@pytest.mark.parametrize("doc_type", TEMPLATED_TYPES)
def test_no_template_key_is_swallowed_by_the_canonical_formatter(doc_type):
    """format_template_json renames some alias keys (e.g. 'loan_account_no') and drops the
    original; a template key with such a name would never reach the UI."""
    for field in get_doc_template(doc_type).fields:
        assert field.key in format_template_json({field.key: "X"}), field.key


@pytest.mark.parametrize("alias", ["sanction_letter", "sanction", "LOAN_123_Sanction_Letter.pdf"])
def test_aliases_and_filenames_resolve_to_the_sanction_template(alias):
    assert get_doc_template(alias).doc_type == "sanction_letter"


@pytest.mark.parametrize("alias", ["kfs", "key_fact_statement", "application_form", "loan_application", "appform"])
def test_kfs_and_app_form_aliases_resolve(alias):
    assert get_doc_template(alias) is not None


@pytest.mark.parametrize("doc_type", ["aadhaar", "pan", "vkyc", "misc", "", None, "../../etc/passwd", "UNKNOWN TYPE"])
def test_types_without_a_template_return_none(doc_type):
    assert get_doc_template(doc_type) is None


# ── Prompt generation ─────────────────────────────────────────────────────

def test_sanction_prompt_is_generated_from_the_template():
    template = get_doc_template("sanction_letter")
    prompt = get_system_prompt("sanction_letter")
    assert prompt == build_system_prompt(template)
    positions = [prompt.index(f'"{f.key}":') for f in template.fields]
    assert positions == sorted(positions), "schema keys must appear in template order"
    assert "apr_percent" in prompt and "Never copy one into the other" in prompt


def test_prompt_schema_block_is_valid_json_with_exactly_the_template_keys():
    template = get_doc_template("sanction_letter")
    prompt = build_system_prompt(template)
    schema = json.loads(prompt[prompt.index("{"):])
    assert list(schema) == [f.key for f in template.fields]
    assert all(v.endswith("or null>") for v in schema.values())


@pytest.mark.parametrize("doc_type", TEMPLATED_TYPES)
def test_every_template_generates_a_prompt_with_all_its_keys(doc_type):
    template = get_doc_template(doc_type)
    prompt = get_system_prompt(doc_type)
    schema = json.loads(prompt[prompt.index("{"):])
    assert list(schema) == [f.key for f in template.fields]


def test_boolean_fields_ask_for_true_or_false():
    schema_prompt = get_system_prompt("kfs")
    consent_line = next(line for line in schema_prompt.splitlines() if '"customer_consent"' in line)
    assert "true or false" in consent_line


@pytest.mark.parametrize("doc_type", ["aadhaar", "pan", "bt_details", "vkyc", "account_statement", "disbursal_memo"])
def test_every_other_type_uses_the_all_key_value_pairs_prompt(doc_type):
    from pipeline.engines.prompts import misc

    assert get_system_prompt(doc_type) == misc.SYSTEM_PROMPT


# ── UI JSON ───────────────────────────────────────────────────────────────

def test_ui_json_uses_labels_in_order_and_nulls_missing_fields():
    fields = {"applicant_name": "RAHUL SHARMA", "loan_amount": "500000", "loan_validity": "48"}
    ui = build_ui_json("sanction_letter", fields)
    assert list(ui) == ["document_type", *SANCTION_UI_LABELS]
    assert ui["document_type"] == "Sanction Letter"
    assert ui["Name"] == "RAHUL SHARMA"
    assert ui["Loan Amount (Rs)"] == "500000"
    assert ui["Tenor (Months)"] == "48"
    assert ui["Sanction Number"] is None


def test_ui_json_leaves_out_hidden_and_unrelated_keys():
    fields = {"irr_percent": "18.5", "emi": "12000", "aadhaar_number": "1234", "applicant_name": "A"}
    ui = build_ui_json("sanction_letter", fields)
    assert set(ui) == {"document_type", *SANCTION_UI_LABELS}
    assert "18.5" not in ui.values() and "1234" not in ui.values()


def test_ui_json_for_empty_extraction_is_all_null():
    assert build_ui_json("sanction_letter", None) == _empty_ui_json("sanction_letter")


def test_ui_json_for_types_without_a_template_is_the_key_value_pairs_found():
    fields = {"applicant_name": "X", "pan_number": "ABCDE1234F", "dob": None, "issuing_authority": "ITD"}
    assert build_ui_json("pan", fields) == {
        "document_type": "PAN Card",
        "applicant_name": "X",
        "pan_number": "ABCDE1234F",
        "customer_consent": False,
        "issuing_authority": "ITD",
    }


def test_ui_json_for_types_without_a_template_and_nothing_found():
    assert build_ui_json("vkyc", {}) == {"document_type": "VKYC Audit Trail", "customer_consent": False}


def test_document_type_from_the_llm_never_overrides_the_detected_type():
    ui = build_ui_json("pan", {"document_type": "Sanction Letter", "pan_number": "ABCDE1234F"})
    assert ui["document_type"] == "PAN Card"
    assert list(ui)[0] == "document_type"


def test_unknown_type_keeps_its_own_name_and_no_type_means_no_key():
    assert build_ui_json("Oom Digital Test", {"a": "1"})["document_type"] == "Oom Digital Test"
    assert "document_type" not in build_ui_json(None, {"a": "1"})


def test_document_type_is_reserved_in_templates(tmp_path):
    path = _write_template(
        tmp_path, "sanction_letter",
        "doc_type: sanction_letter\ndisplay_name: S\nintro: x\nfields:\n"
        "  - {key: document_type, label: Type, description: y}\n",
    )
    with pytest.raises(ValidationError, match="reserved"):
        load_template_file(path)


@pytest.mark.parametrize("doc_type", TEMPLATED_TYPES)
def test_empty_extraction_gives_every_template_label_as_null(doc_type):
    assert build_ui_json(doc_type, None) == _empty_ui_json(doc_type)


def test_format_ui_json_text_round_trips_and_keeps_rupee_symbol_readable():
    text = format_ui_json_text("sanction_letter", {"loan_amount": "₹5,00,000"})
    assert "₹5,00,000" in text
    assert json.loads(text)["Loan Amount (Rs)"] == "₹5,00,000"


def test_ui_label_for():
    assert ui_label_for("sanction_letter", "application_no") == "Ref. Number: LOS ID"
    assert ui_label_for("sanction_letter", "not_a_field") is None
    assert ui_label_for("kfs", "loan_proposal_no") == "Loan Proposal / Account No"
    assert ui_label_for("vkyc", "applicant_name") is None


# ── Invalid templates fail loudly ─────────────────────────────────────────

_VALID_FIELD = "  - {key: applicant_name, label: Name, description: name}\n"


def test_duplicate_keys_are_rejected(tmp_path):
    path = _write_template(
        tmp_path, "sanction_letter",
        "doc_type: sanction_letter\ndisplay_name: S\nintro: x\nfields:\n" + _VALID_FIELD
        + "  - {key: applicant_name, label: Other, description: y}\n",
    )
    with pytest.raises(ValidationError, match="duplicate keys"):
        load_template_file(path)


def test_duplicate_ui_labels_are_rejected(tmp_path):
    path = _write_template(
        tmp_path, "sanction_letter",
        "doc_type: sanction_letter\ndisplay_name: S\nintro: x\nfields:\n" + _VALID_FIELD
        + "  - {key: other, label: Name, description: y}\n",
    )
    with pytest.raises(ValidationError, match="duplicate UI labels"):
        load_template_file(path)


def test_unknown_field_type_is_rejected(tmp_path):
    path = _write_template(
        tmp_path, "sanction_letter",
        "doc_type: sanction_letter\ndisplay_name: S\nintro: x\nfields:\n"
        "  - {key: a, label: A, type: currency, description: y}\n",
    )
    with pytest.raises(ValidationError):
        load_template_file(path)


def test_empty_fields_are_rejected(tmp_path):
    path = _write_template(tmp_path, "sanction_letter", "doc_type: sanction_letter\ndisplay_name: S\nintro: x\nfields: []\n")
    with pytest.raises(ValidationError, match="defines no fields"):
        load_template_file(path)


def test_doc_type_must_match_file_name(tmp_path):
    path = _write_template(tmp_path, "kfs", "doc_type: sanction_letter\ndisplay_name: S\nintro: x\nfields:\n" + _VALID_FIELD)
    with pytest.raises(ValueError, match="must match the file name"):
        load_template_file(path)


def test_non_mapping_yaml_is_rejected(tmp_path):
    path = _write_template(tmp_path, "sanction_letter", "- just\n- a list\n")
    with pytest.raises(ValueError, match="YAML mapping"):
        load_template_file(path)


def test_a_new_template_file_is_picked_up_without_code_changes(tmp_path, monkeypatch):
    _write_template(
        tmp_path, "vkyc",
        "doc_type: vkyc\ndisplay_name: VKYC\nintro: VKYC expert.\nfields:\n"
        "  - {key: loan_amount, label: Loan Amount, type: number, description: amount}\n",
    )
    monkeypatch.setattr(doc_templates, "TEMPLATE_DIR", tmp_path)
    doc_templates._load_by_canonical_type.cache_clear()
    try:
        assert build_ui_json("vkyc", {"loan_amount": "100"}) == {"document_type": "VKYC", "Loan Amount": "100"}
        assert '"loan_amount"' in get_system_prompt("vkyc")
    finally:
        doc_templates._load_by_canonical_type.cache_clear()


# ── Field list labels (Fields tab) ────────────────────────────────────────

def test_fields_tab_uses_template_labels_and_order():
    from app.services.registry.normalizer import parse_extracted_fields

    llm_meta = {"aadhaar_number": "1234", "loan_amount": "500000", "applicant_name": "RAHUL"}
    fields = parse_extracted_fields("DOC_1", {}, llm_meta, doc_type="sanction_letter")
    assert [(f["id"], f["name"]) for f in fields] == [
        ("llm-applicant_name", "Name"),
        ("llm-loan_amount", "Loan Amount (Rs)"),
        ("llm-aadhaar_number", "Aadhaar Number"),
    ]


def test_fields_tab_without_doc_type_is_unchanged():
    from app.services.registry.normalizer import parse_extracted_fields

    fields = parse_extracted_fields("DOC_1", {}, {"loan_amount": "1", "applicant_name": "R"})
    assert [f["name"] for f in fields] == ["Loan Amount", "Applicant Name"]


# ── End to end through the extractor ──────────────────────────────────────

def test_llm_extraction_sends_the_template_prompt_and_keeps_new_sanction_keys(monkeypatch):
    captured = {}

    def fake_invoke(**kwargs):
        captured.update(kwargs)
        return {
            "applicant_name": "RAHUL SHARMA",
            "sanction_number": "SN-2026-0042",
            "apr_percent": "24.35",
            "emi_payment_date": "5th of every month",
            "loan_amount": "500000",
            "irr_percent": "18.5",
        }

    monkeypatch.setattr("pipeline.engines.llm_field_extractor.LLM_API_KEY", "sk-test")
    monkeypatch.setattr("pipeline.engines.llm_client.invoke_llm_json", fake_invoke)

    result = llm_extract_fields("sanction_letter", "SANCTION LETTER ...", "DOC_S1")

    assert captured["system_prompt"] == get_system_prompt("sanction_letter")
    assert result["irr_percent"] == "18.5"  # still available to the LOS checks
    ui = build_ui_json("sanction_letter", result)
    assert ui["Sanction Number"] == "SN-2026-0042"
    assert ui["Annual Percentage Rate (APR) (%)"] == "24.35"
    assert ui["EMI Payment Date"] == "5th of every month"
    assert "18.5" not in ui.values()


def test_kfs_extraction_end_to_end_keeps_check_keys_and_defaults_consent(monkeypatch):
    def fake_invoke(**kwargs):
        assert kwargs["system_prompt"] == get_system_prompt("kfs")
        return {
            "applicant_name": "PRIYA NAIR",
            "loan_proposal_no": "LP-99812",
            "loan_amount": "300000",
            "irr_percent": "17.0",
            "interest_type": "Fixed",
            "apr_percent": "19.2",
            "emi": "10850",
            "BPI": "412",
            "customer_consent": None,
        }

    monkeypatch.setattr("pipeline.engines.llm_field_extractor.LLM_API_KEY", "sk-test")
    monkeypatch.setattr("pipeline.engines.llm_client.invoke_llm_json", fake_invoke)

    result = llm_extract_fields("kfs", "KEY FACT STATEMENT ...", "DOC_K1")

    assert result["emi"] == "10850" and result["BPI"] == "412"  # still available to the LOS checks
    assert result["customer_consent"] is False
    ui = build_ui_json("kfs", result)
    assert list(ui) == ["document_type", *KFS_UI_LABELS]
    assert ui["Loan Proposal / Account No"] == "LP-99812"
    assert ui["Interest Rate (%)"] == "17.0" and ui["APR (%)"] == "19.2"
    assert ui["Customer Consent"] is False
    assert "10850" not in ui.values()


# ── Documents whose LLM extraction returned nothing ───────────────────────

@pytest.mark.parametrize(
    ("filename", "detected_type", "doc_type"),
    [
        ("KFSReport.pdf", "Key Fact Statement (KFS)", "kfs"),
        ("app.pdf", "Application Form", "application_form"),
        ("LOAN_1_Sanction_Letter.pdf", "Sanction Letter", "sanction_letter"),
    ],
)
def test_templated_type_with_no_extraction_still_shows_its_template(filename, detected_type, doc_type):
    from app.services.registry.normalizer import normalize_uploaded_record

    parsed = {"text": "scanned text", "custom_metadata": {}, "pages": [{}]}
    rec = normalize_uploaded_record(
        doc_id="DOC_E", filename=filename, detected_type=detected_type, assoc_case="GENERAL", parsed_result=parsed
    )
    assert json.loads(rec["formattedText"]) == _empty_ui_json(doc_type)


def test_untemplated_type_with_no_extraction_leaves_json_view_to_the_found_pairs():
    from app.services.registry.normalizer import normalize_uploaded_record

    parsed = {"text": "scanned text", "custom_metadata": {}, "pages": [{}]}
    rec = normalize_uploaded_record(
        doc_id="DOC_P", filename="PAN CARD.pdf", detected_type="PAN Card", assoc_case="GENERAL", parsed_result=parsed
    )
    assert rec["formattedText"] == ""


# ── Co-applicants and nested (object) fields ──────────────────────────────

def test_app_form_has_two_co_applicants_with_present_residence_and_address():
    keys = [f.key for f in get_doc_template("application_form").fields]
    for n in (1, 2):
        assert f"co_applicant_{n}_present_residence_type" in keys
        assert f"co_applicant_{n}_present_address" in keys
    assert not any(k.startswith("co_applicant_3") for k in keys)


def test_references_are_objects_with_name_relationship_and_address():
    template = get_doc_template("application_form")
    for key in ("reference_1", "reference_2"):
        field = next(f for f in template.fields if f.key == key)
        assert field.type == "object"
        assert [s.key for s in field.fields] == ["name", "relationship", "residential_address"]


def test_prompt_asks_for_nested_reference_objects():
    prompt = get_system_prompt("application_form")
    schema = json.loads(prompt[prompt.index("{"):])
    assert list(schema["reference_1"]) == ["name", "relationship", "residential_address"]
    assert all(v.endswith("or null>") for v in schema["reference_2"].values())
    assert "return an object with exactly those sub-keys" in prompt
    assert "return an object with exactly those sub-keys" not in get_system_prompt("kfs")


def test_ui_json_nests_reference_sub_labels():
    ui = build_ui_json("application_form", {
        "reference_1": {"name": "Amit Kumar", "relationship": "Friend", "residential_address": "12 MG Road, Pune"},
        "co_applicant_2_present_address": "5 FC Road, Pune",
    })
    assert ui["Reference 1"] == {"Name": "Amit Kumar", "Relationship": "Friend", "Residential Address": "12 MG Road, Pune"}
    assert ui["Reference 2"] == EMPTY_REFERENCE
    assert ui["Co-Applicant 2 Present Address"] == "5 FC Road, Pune"
    assert ui["Co-Applicant 1 Present Address"] is None


def test_ui_json_keeps_old_flat_reference_string_instead_of_dropping_it():
    ui = build_ui_json("application_form", {"reference_1": "Amit Kumar, Friend, Pune"})
    assert ui["Reference 1"] == "Amit Kumar, Friend, Pune"


def test_ui_json_ignores_unknown_sub_keys_and_fills_missing_ones_with_null():
    ui = build_ui_json("application_form", {"reference_1": {"name": "A", "phone": "999"}})
    assert ui["Reference 1"] == {"Name": "A", "Relationship": None, "Residential Address": None}


def test_ui_label_for_sub_fields():
    assert ui_label_for("application_form", "reference_1.relationship") == "Reference 1 - Relationship"
    assert ui_label_for("application_form", "reference_1.phone") is None
    assert ui_label_for("application_form", "co_applicant_1_present_address") == "Co-Applicant 1 Present Address"


def test_fields_tab_lists_each_reference_sub_field_with_its_location():
    from app.services.registry.normalizer import parse_extracted_fields

    parsed = {"custom_metadata": {"field_locations": {
        "reference_1.name": {"bbox": [1, 2, 3, 4], "page": 2, "location_status": "resolved", "confidence": 0.9},
    }}}
    llm_meta = {"reference_1": {"name": "Amit Kumar", "relationship": "Friend", "residential_address": None}}
    fields = parse_extracted_fields("DOC_R", parsed, llm_meta, doc_type="application_form")
    rows = {f["id"]: f for f in fields}
    assert set(rows) == {"llm-reference_1.name", "llm-reference_1.relationship"}
    assert rows["llm-reference_1.name"]["name"] == "Reference 1 - Name"
    assert rows["llm-reference_1.name"]["value"] == "Amit Kumar"
    assert rows["llm-reference_1.name"]["page"] == 2 and rows["llm-reference_1.name"]["bbox"] == [1, 2, 3, 4]


def test_location_resolver_searches_each_sub_field_separately():
    from idp.services.extraction.field_location_resolver import _flatten_nested_fields

    assert _flatten_nested_fields({"a": "1", "reference_1": {"name": "Amit", "relationship": None}}) == [
        ("a", "1"), ("reference_1.name", "Amit"), ("reference_1.relationship", None),
    ]


@pytest.mark.parametrize(
    ("field_yaml", "match"),
    [
        ("  - {key: r, label: R, type: object, description: y}\n", "needs at least one sub-field"),
        ("  - {key: r, label: R, description: y, fields: [{key: n, label: N, description: z}]}\n", "use type: object"),
        ("  - {key: r, label: R, type: object, description: y, fields: [{key: n, label: N, description: z}, {key: n, label: M, description: z}]}\n", "duplicate sub-keys"),
        ("  - {key: r, label: R, type: object, description: y, fields: [{key: n, label: N, description: z}, {key: m, label: N, description: z}]}\n", "duplicate sub-labels"),
        ("  - {key: r, label: R, type: object, description: y, fields: [{key: n, label: N, type: object, description: z}]}\n", "fields.0.type"),
    ],
)
def test_invalid_object_fields_are_rejected(tmp_path, field_yaml, match):
    path = _write_template(tmp_path, "sanction_letter", "doc_type: sanction_letter\ndisplay_name: S\nintro: x\nfields:\n" + field_yaml)
    with pytest.raises(ValidationError, match=match):
        load_template_file(path)


def test_app_form_nested_extraction_end_to_end(monkeypatch):
    def fake_invoke(**kwargs):
        return {
            "applicant_name": "PRIYA NAIR",
            "co_applicant_1_present_residence_type": "Rented",
            "co_applicant_1_present_address": "5 FC Road, Pune",
            "reference_1": {"name": "Amit Kumar", "relationship": "Friend", "residential_address": "12 MG Road, Pune"},
            "reference_2": None,
        }

    monkeypatch.setattr("pipeline.engines.llm_field_extractor.LLM_API_KEY", "sk-test")
    monkeypatch.setattr("pipeline.engines.llm_client.invoke_llm_json", fake_invoke)

    result = llm_extract_fields("application_form", "APPLICATION FORM ...", "DOC_A1")
    ui = build_ui_json("application_form", result)
    assert ui["Co-Applicant 1 Present Residence"] == "Rented"
    assert ui["Reference 1"]["Relationship"] == "Friend"
    assert ui["Reference 2"] == EMPTY_REFERENCE
