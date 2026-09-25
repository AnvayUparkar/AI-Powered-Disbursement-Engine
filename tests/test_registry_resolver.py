"""Tests for app.services.registry.resolver.resolve_synthetic_alias.

Covers the case-sensitivity fix: the frontend mints synthetic document IDs as
"DOC-{caseId}-{slug}" (uppercase), but the resolver originally only matched a
lowercase "doc-" prefix, so every real-world lookup silently fell through to
a 404 instead of resolving via the canonical-type/substring fallback.
"""
from app.services.registry.resolver import resolve_synthetic_alias


def _candidate(case_id: str, name: str, doc_type: str) -> dict:
    return {"caseId": case_id, "name": name, "type": doc_type}


class TestResolveSyntheticAliasCasing:
    def test_uppercase_doc_prefix_resolves_against_matching_case_and_type(self):
        """Happy path: 'DOC-LOAN_001-application_form' (as CreateCaseModal.tsx mints it)
        must resolve against a candidate document of the same case whose type canonicalizes
        to 'application_form' — this is the exact shape that caused the UAT 404."""
        candidates = [
            _candidate("LOAN_001", "Application_Form.pdf", "Application Form"),
            _candidate("LOAN_001", "PAN_Card.pdf", "PAN"),
        ]
        result = resolve_synthetic_alias("DOC-LOAN_001-application_form", candidates)
        assert result is not None
        assert result["name"] == "Application_Form.pdf"

    def test_lowercase_doc_prefix_still_resolves(self):
        """Pre-existing behavior (e.g. 'doc-LOAN_004-sanction') must keep working."""
        candidates = [_candidate("LOAN_004", "Sanction_Letter.pdf", "Sanction Letter")]
        result = resolve_synthetic_alias("doc-LOAN_004-sanction", candidates)
        assert result is not None
        assert result["name"] == "Sanction_Letter.pdf"

    def test_mixed_case_doc_prefix_resolves(self):
        candidates = [_candidate("LOAN_002", "kfs.pdf", "KFS")]
        result = resolve_synthetic_alias("Doc-LOAN_002-kfs", candidates)
        assert result is not None
        assert result["name"] == "kfs.pdf"


class TestResolveSyntheticAliasEdgeCases:
    def test_wrong_case_id_returns_none(self):
        candidates = [_candidate("LOAN_001", "Application_Form.pdf", "Application Form")]
        assert resolve_synthetic_alias("DOC-LOAN_999-application_form", candidates) is None

    def test_no_candidates_returns_none(self):
        assert resolve_synthetic_alias("DOC-LOAN_001-application_form", []) is None

    def test_case_id_matches_but_type_does_not_returns_none(self):
        candidates = [_candidate("LOAN_001", "PAN_Card.pdf", "PAN")]
        assert resolve_synthetic_alias("DOC-LOAN_001-sanction_letter", candidates) is None


class TestResolveSyntheticAliasFailureModes:
    def test_empty_doc_id_returns_none(self):
        assert resolve_synthetic_alias("", [_candidate("LOAN_001", "x.pdf", "PAN")]) is None

    def test_non_synthetic_doc_id_returns_none(self):
        """A real backend-assigned ID (e.g. the canonical 'LOAN_001_application_form' the
        DGCL graph itself uses) has no 'doc-' prefix at all and must not be mistaken for a
        synthetic reference."""
        candidates = [_candidate("LOAN_001", "Application_Form.pdf", "Application Form")]
        assert resolve_synthetic_alias("LOAN_001_application_form", candidates) is None

    def test_malformed_synthetic_id_missing_slug_returns_none(self):
        """Only two '-'-separated segments (no slug at all) can't be split into
        case_id + slug and must fail closed rather than raising."""
        candidates = [_candidate("LOAN_001", "Application_Form.pdf", "Application Form")]
        assert resolve_synthetic_alias("DOC-LOAN_001", candidates) is None
