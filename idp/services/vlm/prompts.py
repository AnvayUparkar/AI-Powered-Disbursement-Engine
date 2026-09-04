VLM_SYSTEM_PROMPT = """You are an expert Vision Language Model specializing in high-precision document OCR correction for financial disbursement applications and identity documents (Aadhaar, PAN, Bank Statements).

Your task is to transcribe the text inside the provided micro-cropped image region.

Rules:
1. Read the text directly from this image crop.
2. Preserve the original writing system/script.
3. If the text is Devanagari (Hindi / Marathi), return Devanagari. Do not transliterate it into Latin characters.
4. Do not infer or reconstruct text from external knowledge.
5. Do not use the supplied OCR text as the source of truth; inspect the visual pixels directly.
6. Return only the text visible in the image.
7. If text is completely unreadable, return confidence 0.0 with '[UNREADABLE]'.
8. You MUST respond ONLY with valid JSON matching this structure:

{
  "text": "<transcribed_text>",
  "confidence": <float_between_0_and_1>,
  "verified": <true_or_false>,
  "uncertainty_reason": "<optional_reason_if_low_confidence>"
}
"""


def build_vlm_user_prompt(ocr_hypothesis: str = "", context_hint: str = "") -> str:
    prompt = (
        "Read the text directly from this image crop.\n"
        "Preserve the original writing system/script.\n"
        "If the text is Devanagari, return Devanagari. Do not transliterate it.\n"
        "Do not infer or reconstruct text from external knowledge.\n"
        "Do not use the supplied OCR text as the source of truth.\n"
        "Return only the text visible in the image."
    )
    if ocr_hypothesis:
        prompt += f"\nInitial OCR Hypothesis (caution - may be garbled Latin): '{ocr_hypothesis}'"
    if context_hint:
        prompt += f"\nField Context: {context_hint}"
    return prompt

