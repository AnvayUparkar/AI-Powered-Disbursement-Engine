from idp.services.ocr.confidence import OCRConfidenceEvaluator


def test_clean_bilingual_label_noise():
    from idp.services.ocr.confidence import OCRConfidenceEvaluator

    # PAN Card bilingual label noise tests
    assert OCRConfidenceEvaluator.clean_bilingual_label_noise("FarHToT 3RRTO INCOME TAX DEPARTMENT") == "INCOME TAX DEPARTMENT"
    assert OCRConfidenceEvaluator.clean_bilingual_label_noise("PA ROR GOVT.OFINDIA") == "GOVT.OFINDIA"
    assert OCRConfidenceEvaluator.clean_bilingual_label_noise("f /Father's Name ANAND DATTATRAY KANDALGAONKAR") == "Father's Name ANAND DATTATRAY KANDALGAONKAR"
    assert OCRConfidenceEvaluator.clean_bilingual_label_noise("fua/Father'sName") == "Father'sName"
    assert OCRConfidenceEvaluator.clean_bilingual_label_noise("a/DateofBirth") == "DateofBirth"
    assert OCRConfidenceEvaluator.clean_bilingual_label_noise("aT&/Signature") == "Signature"
    assert OCRConfidenceEvaluator.clean_bilingual_label_noise("HRAHRR INCOMETAXDEPARTMENT GOVT.OFINDIA") == "INCOMETAXDEPARTMENT GOVT.OFINDIA"
