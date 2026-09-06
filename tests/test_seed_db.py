import sqlite3

import pytest

from poc_data.los.loans.seed_db import seed


CSV_HEADER = "Application No,Customer Name,Age,Date of Birth,Gender,Address,Loan Amount,Tenure,EMI,BPI Charges,IRR (%)"


def _write_csv(path, rows):
    path.write_text("\n".join([CSV_HEADER, *rows]) + "\n", encoding="utf-8")


def test_seed_rebuilds_database_and_preserves_all_source_fields(tmp_path):
    csv_path = tmp_path / "Test Data.csv"
    db_path = tmp_path / "loans.db"
    _write_csv(csv_path, [
        "APP001,Asha Rao,31,12/04/95,Female,1 Main Street,250000,24,12000,500,14.5",
        "APP002,Ravi Shah,42,08/09/82,Male,2 Lake Road,400000,36,15000,700,16",
    ])

    seed(csv_path, db_path)

    connection = sqlite3.connect(db_path)
    rows = connection.execute(
        "SELECT loan_id, applicant_name, loan_amount, age, tenure, emi, bpi_charges, irr_percent "
        "FROM loan_applications ORDER BY loan_id"
    ).fetchall()
    connection.close()
    assert rows == [
        ("APP001", "Asha Rao", 250000.0, 31, 24, 12000.0, 500.0, 14.5),
        ("APP002", "Ravi Shah", 400000.0, 42, 36, 15000.0, 700.0, 16.0),
    ]


def test_seed_removes_records_no_longer_in_csv(tmp_path):
    csv_path = tmp_path / "Test Data.csv"
    db_path = tmp_path / "loans.db"
    _write_csv(csv_path, ["APP001,Asha Rao,31,12/04/95,Female,1 Main Street,250000,24,12000,500,14.5"])
    seed(csv_path, db_path)

    _write_csv(csv_path, ["APP002,Ravi Shah,42,08/09/82,Male,2 Lake Road,400000,36,15000,700,16"])
    seed(csv_path, db_path)

    connection = sqlite3.connect(db_path)
    rows = connection.execute("SELECT loan_id FROM loan_applications").fetchall()
    connection.close()
    assert rows == [("APP002",)]


def test_seed_rejects_missing_required_column_without_replacing_database(tmp_path):
    csv_path = tmp_path / "Test Data.csv"
    db_path = tmp_path / "loans.db"
    _write_csv(csv_path, ["APP001,Asha Rao,31,12/04/95,Female,1 Main Street,250000,24,12000,500,14.5"])
    seed(csv_path, db_path)

    csv_path.write_text(
        "Application No,Customer Name,Age,Date of Birth,Address,Loan Amount\n"
        "APP002,Ravi Shah,42,08/09/82,2 Lake Road,400000\n",
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="missing required columns"):
        seed(csv_path, db_path)

    connection = sqlite3.connect(db_path)
    rows = connection.execute("SELECT loan_id FROM loan_applications").fetchall()
    connection.close()
    assert rows == [("APP001",)]


def test_seed_preserves_supplied_legacy_fields_and_nulls_missing_values(tmp_path):
    csv_path = tmp_path / "Test Data.csv"
    db_path = tmp_path / "loans.db"
    csv_path.write_text(
        "Application No,Customer Name,Age,Date of Birth,Gender,Address,Loan Amount,Tenure,EMI,BPI Charges,IRR (%),"
        "Applicant Mobile NO,Applicant Pan Number,Fathers Name,Applicant Bank Account NO,Loan Type,Aadhaar No,Application Date\n"
        "APP003,Soumya N V,,21/10/1990,Male,,285300,24,,,,9916591658,ECZPS3281J,SURESH G S,055801575041,"
        "Personal Loan,XXXXXXXX 0341,03/ 08/ 2026\n",
        encoding="utf-8",
    )

    seed(csv_path, db_path)

    connection = sqlite3.connect(db_path)
    row = connection.execute(
        "SELECT applicant_mobile_no, applicant_pan_number, fathers_name, applicant_bank_account_no, "
        "loan_type, aadhaar_no, application_date, age, emi, bpi_charges, irr_percent, current_address "
        "FROM loan_applications WHERE loan_id = 'APP003'"
    ).fetchone()
    connection.close()
    assert row == (
        "9916591658", "ECZPS3281J", "SURESH G S", "055801575041", "Personal Loan",
        "XXXXXXXX 0341", "03/ 08/ 2026", None, None, None, None, None,
    )