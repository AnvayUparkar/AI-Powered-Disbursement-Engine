# AI Engineering & Development Guidelines

This document defines the strict coding rules, execution standards, and testing mandates that Antigravity must follow across this entire repository.

---

## 1. Zero Unsolicited Changes (Surgical Precision)

- **Strict Scope Boundaries:** Modify *only* the specific files, functions, and lines required to fulfill the user's explicit request.
- **Preserve Existing Code & Comments:** Never delete, rewrite, or "clean up" unrelated comments, docstrings, formatting, or working code.
- **No Drive-by Refactoring:** Do not modernize, re-architect, or optimize code outside the direct scope of the task unless explicitly instructed.
- **Match Existing Style:** Follow existing project conventions, naming schemes, indentation, and structure.
- **Orphan Cleanup:** Clean up only imports, variables, or functions that *your own* modifications have rendered obsolete. Never remove pre-existing unused code without permission.

---

## 2. Mandatory Real Test Cases (No Placeholders)

- **Comprehensive Coverage:** Every new function, feature, pipeline node, or bugfix must be accompanied by real, actionable test cases in the `tests/` directory.
- **Zero Dummy Tests:** Never write trivial assertions (e.g., `assert True`, `assert result is not None` without validating properties, or unverified dummy mocks).
- **Test Scenarios Required for Every Change:**
  1. **Happy Path:** Expected valid input producing precise, deterministic output.
  2. **Edge Cases & Boundary Conditions:** Empty data, zero values, extreme thresholds, missing optional fields.
  3. **Failure & Error Modes:** Invalid inputs, missing required fields, exception handling validation (e.g., `pytest.raises(...)`).
- **Deterministic & Isolated:** Use realistic mock fixtures, synthetic test data, or isolated temporary directories (`tmp_path`). Tests must run deterministically and fast without network dependencies.
- **Automated Verification:** Always execute the test suite (e.g., `pytest tests/`) after making changes to verify that all existing and new tests pass with zero regressions.

---

## 3. Best Coding Practices & Standards

### Architecture & Modularity
- **Separation of Concerns:** Keep pipeline stages, scoring logic, data ingestion, configuration, and API endpoints cleanly decoupled.
- **Config-Driven Behavior:** Never hardcode thresholds, file paths, or scoring weights in business logic. Always reference [config.py](file:///d:/Projects/Automated%20Disbursment%20Scorecard/config.py) or environment variables.
- **Immutability & Predictability:** Ensure data transformations (Pandas/Polars/dicts) are pure and predictable, avoiding unintended in-place side effects on shared objects.

### Python Standards
- **Explicit Type Hints:** Use typing annotations (`typing.Dict`, `typing.List`, `typing.Optional`, `typing.Union`, or Python 3.10+ union types `X | None`) for all function signatures and return types.
- **Robust Error Handling:** Never catch bare `Exception:` or pass silently (`except: pass`). Always catch specific exceptions, log the context, and re-raise or return structured failure states.
- **Logging Over Printing:** Use structured Python logging (`logger.info`, `logger.warning`, `logger.error`) rather than raw `print()` statements in production/pipeline modules.
- **Fail Fast, Validate Early:** Validate input payloads and schemas at boundaries (using Pydantic models, schema validators, or explicit validation checks) before processing.

---

## 4. Execution Workflow

When fulfilling any task:
1. **Analyze:** Inspect relevant files, existing tests, and configurations before touching any code.
2. **Implement:** Make minimal, focused, surgical edits directly addressing the objective.
3. **Test:** Add or update concrete unit and integration tests covering the changes.
4. **Verify:** Run the automated test runner (`pytest`) to confirm all tests pass cleanly.

## Response Rules

- Be concise. No preamble, no summaries of what you are about to do.
- Show only changed lines/sections — do not echo entire files.
- Do not explain obvious changes. Explain only non-obvious reasoning.
- After completing a task, stop. Do not chain into unrequested follow-ups.