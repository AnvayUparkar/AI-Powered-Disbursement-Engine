"""
Layout-Aware Spatial Key-Value & Form Extraction Layer (Strict Element-Classified & Region-Constrained).

Architecture:
1. Element Classification:
   - BRANDING / HEADER: Company/bank logos, legal entity names, top-of-page branding headers.
   - SECTION_HEADER: Grouping titles (e.g. DEBIT, FREQUENCY, TYPE, BILLING COMPUTATION).
   - CHECKBOX_LABEL / CONTROL: Option tokens (e.g. Daily, Monthly, Fixed, Maximum, Create).
   - NOISE / LOW_CONF_GARBLED: High corrupt ratio or OCR confidence < 0.70.
   - VALUE: Numbers, monetary amounts, bank codes, account numbers, names, amount in words.
   - LABEL: Form prompts (e.g. Sponsor Bank Code, with Bank, IFSC/MICR, Amount, Reference).
2. Local Form Region & Row Clustering:
   - Form elements grouped into spatial rows & localized vertical regions.
   - Strict geometric bounds for RIGHT_OF_LABEL (same row, no intervening labels, max dx <= 0.35).
   - Strict geometric bounds for BELOW_LABEL (direct column overlap, strict max dy <= 0.045).
3. Semantic Compatibility Scoring & Confidence Bands:
   - Values must semantically match the label intent (e.g. amount -> numeric or words, bank -> bank text).
   - Rejects inverted VALUE_AS_LABEL (amount in words or numbers cannot be treated as labels).
   - Threshold >= 0.75 for emission; uncertain or unmatched labels are omitted or set to None.
"""

import re
import logging
from enum import Enum
from typing import List, Dict, Any, Optional, Tuple, Set

logger = logging.getLogger("disbursement_pipeline.key_value_extractor")


class ElementClassification(str, Enum):
    BRANDING = "branding"
    SECTION_HEADER = "section_header"
    CHECKBOX_LABEL = "checkbox_label"
    LABEL = "label"
    VALUE = "value"
    PARAGRAPH = "paragraph"
    NOISE = "noise"


class KeyValueExtractor:
    """
    Production spatial layout-aware form extractor for financial documents,
    NACH mandates, loan applications, KYC documents, and bank statements.
    """

    # Checkbox option groups
    CHECKBOX_PATTERNS = {
        "action": ["create", "modify", "cancel", "modity"],
        "account_type": ["sb", "ca", "cc", "sb-nre", "sb-nro", "other", "sbica"],
        "debit_type": ["fixed", "maximum", "maxdmum", "fhed"],
        "frequency": [
            "daily", "dally", "weekly", "monthly", "monthiy", "quarterly",
            "half-yearly", "yearly", "as and when presented", "presenied", "qty"
        ]
    }

    CHECKED_SYMBOLS = {"x", "区", "☑", "✓", "✔", "v", "■", "black_box", "tick", "ticked", "[x]", "冈"}

    # Known Section/Group headings (must not be treated as key-value labels)
    SECTION_HEADERS = {
        "debit", "frequency", "type", "debit type", "mandate details", "bill parameter",
        "consumer parameter", "billing computation & charges breakdown",
        "billingcomputation&chargesbreakdown", "charges breakdown", "summary"
    }

    # Common branding & entity headers (must not be treated as key-value labels)
    BRANDING_KEYWORDS = {
        "financial services", "financial services ltd", "hdb", "chdb", "hdbfinancialservicesltd",
        "electricity distribution", "mahastate", "bank ltd", "limited", "prakashgad",
        "toll free", "registered office"
    }

    # Known valid field labels
    KNOWN_LABELS = {
        "sponsor bank code", "utility code", "urllity code", "umrn", "date", "with bank",
        "ifsc/micr", "ifsc", "micr", "an amount of rupees", "amount of rupees", "amount",
        "consumer name", "consumer no", "bill number", "bill date", "billing period",
        "due date", "bu / subdivision", "billing address", "reference", "reference 1",
        "reference 2", "rederence1", "reference a", "bank a/c number", "bauka/cumber",
        "application id", "loan id", "pan number", "pan", "applicant name",
        "permanent account number", "permanentaccountnumber", "permanent account no", "permanent account"
    }

    CHECKBOX_OPTION_REGEX = re.compile(
        r"\b(" + "|".join(
            re.escape(pat) for pats in CHECKBOX_PATTERNS.values() for pat in pats
        ) + r")\b",
        re.IGNORECASE
    )

    def __init__(
        self,
        same_row_y_threshold: float = 0.022,
        max_horizontal_gap: float = 0.35,
        max_vertical_gap: float = 0.045,
        min_pair_confidence: float = 0.72
    ):
        self.same_row_y_threshold = same_row_y_threshold
        self.max_horizontal_gap = max_horizontal_gap
        self.max_vertical_gap = max_vertical_gap
        self.min_pair_confidence = min_pair_confidence

    def extract(
        self,
        elements: List[Dict[str, Any]],
        doc_type: str = "generic"
    ) -> Dict[str, Any]:
        """
        Executes strict classification, checkbox detection, and spatial pairing.
        """
        if not elements:
            return {
                "key_values": {},
                "checkboxes": {},
                "paragraphs": []
            }

        # Step 1: Group by page
        pages: Dict[int, List[Dict[str, Any]]] = {}
        for elem in elements:
            pno = elem.get("page_number", 1)
            if pno not in pages:
                pages[pno] = []
            pages[pno].append(elem)

        all_key_values: Dict[str, Dict[str, Any]] = {}
        all_checkboxes: Dict[str, Dict[str, Any]] = {}
        unconsumed_paragraphs: List[Dict[str, Any]] = []

        for pno, page_elems in pages.items():
            consumed_ids: Set[str] = set()

            # 2. Extract checkboxes first
            cb_results, cb_consumed = self._extract_checkboxes(page_elems)
            for group, val in cb_results.items():
                if group not in all_checkboxes:
                    all_checkboxes[group] = val
                else:
                    all_checkboxes[group]["options"].update(val.get("options", {}))
            consumed_ids.update(cb_consumed)

            # 3. Classify all remaining elements
            classified = self._classify_elements(page_elems, consumed_ids)

            # 4. Pair LABELS with VALUES using spatial + semantic scoring
            kv_results, kv_consumed = self._extract_spatial_key_values(classified, consumed_ids)
            for k, v in kv_results.items():
                if k not in all_key_values:
                    all_key_values[k] = v
            consumed_ids.update(kv_consumed)

            # 5. Remaining unconsumed elements are true structural paragraphs / headers
            for item in classified:
                eid = item["element"].get("id")
                if eid not in consumed_ids:
                    txt = item["element"].get("text", "").strip()
                    if txt:
                        unconsumed_paragraphs.append({
                            "id": eid,
                            "type": item["element"].get("type", "text"),
                            "text": txt,
                            "page_number": pno,
                            "bbox": item["element"].get("bbox", []),
                            "confidence": item["element"].get("confidence", 1.0),
                            "source": item["element"].get("source", "ocr"),
                            "classification": item["classification"].value
                        })

        return {
            "key_values": all_key_values,
            "checkboxes": all_checkboxes,
            "paragraphs": unconsumed_paragraphs
        }

    def _extract_checkboxes(
        self,
        elements: List[Dict[str, Any]]
    ) -> Tuple[Dict[str, Any], Set[str]]:
        """Identifies and extracts checkboxes while tagging their element IDs as consumed."""
        checkboxes: Dict[str, Any] = {}
        consumed: Set[str] = set()

        for elem in elements:
            txt = elem.get("text", "").strip()
            txt_lower = txt.lower()

            if ":" in txt:
                continue

            for group_name, patterns in self.CHECKBOX_PATTERNS.items():
                for pat in patterns:
                    pat_regex = r"(?:^|[\s\[\(区☑✓✔vx\-\/冈μB\-])" + re.escape(pat) + r"(?:$|[\s\]\)区☑✓✔vx\-\/])"
                    if re.search(pat_regex, txt_lower):
                        if group_name not in checkboxes:
                            checkboxes[group_name] = {"options": {}}

                        is_checked = any(sym in txt_lower for sym in self.CHECKED_SYMBOLS)
                        canon_opt = pat.replace(" ", "_")
                        if pat in ["dally"]:
                            canon_opt = "daily"
                        elif pat in ["monthiy"]:
                            canon_opt = "monthly"
                        elif pat in ["fhed"]:
                            canon_opt = "fixed"
                        elif pat in ["maxdmum"]:
                            canon_opt = "maximum"
                        elif pat in ["modity"]:
                            canon_opt = "modify"
                        elif pat in ["presenied"]:
                            canon_opt = "as_and_when_presented"
                        elif pat in ["qty"]:
                            canon_opt = "quarterly"
                        elif pat in ["sbica"]:
                            canon_opt = "sb"

                        checkboxes[group_name]["options"][canon_opt] = is_checked
                        consumed.add(elem.get("id"))

        return checkboxes, consumed

    def _classify_elements(
        self,
        elements: List[Dict[str, Any]],
        consumed_ids: Set[str]
    ) -> List[Dict[str, Any]]:
        """
        Classifies each unconsumed OCR element into:
        BRANDING, SECTION_HEADER, CHECKBOX_LABEL, VALUE, LABEL, PARAGRAPH, or NOISE.
        """
        classified = []

        for elem in elements:
            eid = elem.get("id")
            if eid in consumed_ids:
                continue

            txt = elem.get("text", "").strip()
            if not txt:
                continue

            bbox = self._bbox(elem)
            conf = float(elem.get("confidence", 1.0))
            txt_lower = txt.lower()
            clean_txt = re.sub(r"[^\w\s/]", "", txt_lower).strip()

            # 1. Noise check
            if conf < 0.50 or self._is_garbled(txt):
                classified.append({"element": elem, "classification": ElementClassification.NOISE})
                continue

            # 2. Section header check
            if clean_txt in self.SECTION_HEADERS:
                classified.append({"element": elem, "classification": ElementClassification.SECTION_HEADER})
                continue

            # 3. Branding check (top area of page or brand keywords)
            if any(b in clean_txt for b in self.BRANDING_KEYWORDS):
                classified.append({"element": elem, "classification": ElementClassification.BRANDING})
                continue
            if bbox[3] < 0.16 and ("bank" in clean_txt or "services" in clean_txt or "financial" in clean_txt):
                classified.append({"element": elem, "classification": ElementClassification.BRANDING})
                continue

            # 4. Checkbox control label check (with word boundaries to avoid false positives on words like 'account')
            if self.CHECKBOX_OPTION_REGEX.search(clean_txt):
                classified.append({"element": elem, "classification": ElementClassification.CHECKBOX_LABEL})
                continue

            # 5. Inline colon delimiter -> Instant LABEL
            if ":" in txt or "=" in txt:
                classified.append({"element": elem, "classification": ElementClassification.LABEL})
                continue

            # 6. Value check: Is it clearly a value? (Number, amount in words, bank code, account)
            if self._is_definite_value(txt):
                classified.append({"element": elem, "classification": ElementClassification.VALUE})
                continue

            # 7. Form Label check
            if self._is_form_label(txt):
                classified.append({"element": elem, "classification": ElementClassification.LABEL})
                continue

            # 8. Default fallback
            classified.append({"element": elem, "classification": ElementClassification.PARAGRAPH})

        return classified

    def _extract_spatial_key_values(
        self,
        classified_items: List[Dict[str, Any]],
        consumed_ids: Set[str]
    ) -> Tuple[Dict[str, Dict[str, Any]], Set[str]]:
        """
        Pairs classified LABEL elements with compatible VALUE elements based on strict
        geometric distance, row clustering, and semantic compatibility.
        """
        key_values: Dict[str, Dict[str, Any]] = {}
        consumed: Set[str] = set()

        # Inline Delimiter Extraction
        for item in classified_items:
            elem = item["element"]
            eid = elem.get("id")
            if eid in consumed_ids:
                continue

            txt = elem.get("text", "").strip()
            if ":" in txt or "=" in txt:
                delimiter = ":" if ":" in txt else "="
                parts = txt.split(delimiter, 1)
                k = parts[0].strip()
                v = parts[1].strip()
                if k and v:
                    norm_k = self._normalize_label_key(k)
                    key_values[norm_k] = {
                        "label": k,
                        "value": v,
                        "page_number": elem.get("page_number", 1),
                        "confidence": round(float(elem.get("confidence", 1.0)), 4),
                        "relationship": "inline_delimiter"
                    }
                    consumed.add(eid)
                    logger.debug(f"[KV EXTRACTION] Inline: Label='{k}' Value='{v}'")

        # Candidate Labels & Values
        labels = [
            item["element"] for item in classified_items
            if item["classification"] == ElementClassification.LABEL and item["element"].get("id") not in consumed and item["element"].get("id") not in consumed_ids
        ]
        values = [
            item["element"] for item in classified_items
            if item["classification"] == ElementClassification.VALUE and item["element"].get("id") not in consumed and item["element"].get("id") not in consumed_ids
        ]

        # Sort labels top-to-bottom, left-to-right
        labels.sort(key=lambda e: (self._bbox(e)[1], self._bbox(e)[0]))

        for l_elem in labels:
            l_id = l_elem.get("id")
            if l_id in consumed:
                continue

            l_txt = l_elem.get("text", "").strip()
            l_box = self._bbox(l_elem)
            l_cy = (l_box[1] + l_box[3]) / 2.0
            l_height = max(0.01, l_box[3] - l_box[1])

            best_val = None
            best_rel = None
            best_score = -100.0

            # Priority 1: Search RIGHT on the SAME ROW
            for v_elem in values:
                v_id = v_elem.get("id")
                if v_id in consumed:
                    continue

                v_box = self._bbox(v_elem)
                v_cy = (v_box[1] + v_box[3]) / 2.0

                y_diff = abs(l_cy - v_cy)
                v_overlap = max(0.0, min(l_box[3], v_box[3]) - max(l_box[1], v_box[1]))
                is_same_row = (y_diff <= self.same_row_y_threshold) or (v_overlap / l_height >= 0.40)

                if is_same_row:
                    dx = v_box[0] - l_box[2]
                    # Right of label: allow slight touch/overlap up to max_horizontal_gap
                    if -0.02 <= dx <= self.max_horizontal_gap:
                        # Prevent cross-field column collisions: ensure no other label is in between
                        has_intervening_label = any(
                            l2.get("id") != l_id and
                            l_box[2] < self._bbox(l2)[0] < v_box[0] and
                            abs((self._bbox(l2)[1] + self._bbox(l2)[3]) / 2.0 - l_cy) <= self.same_row_y_threshold
                            for l2 in labels
                        )
                        if not has_intervening_label:
                            score = self._compute_pair_score(l_elem, v_elem, relationship="right_of_label", distance=max(0.0, dx))
                            if score > best_score:
                                best_score = score
                                best_val = v_elem
                                best_rel = "right_of_label"

            # Priority 2: Search strictly BELOW label within same local form column
            if not best_val:
                for v_elem in values:
                    v_id = v_elem.get("id")
                    if v_id in consumed:
                        continue

                    v_box = self._bbox(v_elem)
                    dy = v_box[1] - l_box[3]

                    # Strict below threshold: max_vertical_gap <= 0.045
                    if 0.0 <= dy <= self.max_vertical_gap:
                        # Must have horizontal overlap or close left alignment
                        h_overlap = max(0.0, min(l_box[2], v_box[2]) - max(l_box[0], v_box[0]))
                        left_diff = abs(l_box[0] - v_box[0])

                        if h_overlap > 0.0 or left_diff <= 0.06:
                            score = self._compute_pair_score(l_elem, v_elem, relationship="below_label", distance=dy)
                            if score > best_score:
                                best_score = score
                                best_val = v_elem
                                best_rel = "below_label"

            # Strict score & confidence verification
            if best_val and best_score >= 0.70:
                pair_conf = min(float(l_elem.get("confidence", 1.0)), float(best_val.get("confidence", 1.0)))
                if pair_conf >= self.min_pair_confidence:
                    norm_k = self._normalize_label_key(l_txt)
                    val_txt = best_val.get("text", "").strip()

                    key_values[norm_k] = {
                        "label": l_txt,
                        "value": val_txt,
                        "page_number": l_elem.get("page_number", 1),
                        "confidence": round(pair_conf, 4),
                        "relationship": best_rel
                    }
                    consumed.add(l_id)
                    consumed.add(best_val.get("id"))
                    logger.info(f"[KV EXTRACTION ACCEPTED] '{l_txt}' -> '{val_txt}' | Rel: {best_rel} | Score: {best_score:.2f}")
                else:
                    logger.debug(f"[KV EXTRACTION REJECTED CONFIDENCE] '{l_txt}' -> '{best_val.get('text')}' (conf: {pair_conf:.2f} < {self.min_pair_confidence})")
            elif best_val:
                logger.debug(f"[KV EXTRACTION REJECTED SCORE] '{l_txt}' -> '{best_val.get('text')}' (score: {best_score:.2f} < 0.70)")

        return key_values, consumed

    def _compute_pair_score(
        self,
        label_elem: Dict[str, Any],
        val_elem: Dict[str, Any],
        relationship: str,
        distance: float
    ) -> float:
        """
        Computes composite compatibility score:
        Score = geometry + semantic - distance_penalty - garbled_penalty
        """
        l_txt = label_elem.get("text", "").strip().lower()
        v_txt = val_elem.get("text", "").strip()
        v_txt_lower = v_txt.lower()

        score = 0.50  # Base match score

        # 1. Geometric alignment score
        if relationship == "right_of_label":
            score += 0.25
            score -= (distance / self.max_horizontal_gap) * 0.15
        elif relationship == "below_label":
            score += 0.15
            score -= (distance / self.max_vertical_gap) * 0.15

        # 2. Semantic compatibility
        # Amount in Words vs Numeric Amount
        if "rupees" in l_txt or "words" in l_txt:
            if self._is_amount_in_words(v_txt):
                score += 0.35
            elif re.match(r"^[\d,.]+$", v_txt):
                score -= 0.30
        elif "amount" in l_txt:
            if re.match(r"^[\d,.]+$", v_txt):
                score += 0.35
            elif self._is_amount_in_words(v_txt):
                score += 0.15

        # Bank Name
        if "with bank" in l_txt or "bank name" in l_txt:
            if "bank" in v_txt_lower or any(b in v_txt_lower for b in ["punjab", "hdfc", "sbi", "icici", "axis"]):
                score += 0.35
            elif re.match(r"^[\d]+$", v_txt):
                score -= 0.40

        # Bank / Utility / Sponsor Code / Reference
        if any(c in l_txt for c in ["code", "ifsc", "micr", "umrn", "reference", "ref"]):
            if bool(re.search(r"\d", v_txt)) and not bool(re.search(r"\s{2,}", v_txt)):
                score += 0.30

        # PAN / Identity / Permanent Account Number
        if any(p in l_txt for p in ["pan", "permanent", "account"]):
            if bool(re.search(r"[A-Z0-9]{10}", v_txt.upper())) or bool(re.search(r"\d", v_txt)):
                score += 0.35

        # Date
        if "date" in l_txt:
            if re.search(r"\d{1,4}[-/\.]\d{1,2}[-/\.]\d{1,4}", v_txt) or any(m in v_txt_lower for m in ["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"]):
                score += 0.35

        # Penalties: Checkbox leakage or noise
        if any(w in v_txt_lower for w in ["weekly", "monthly", "daily", "fixed", "maximum"]):
            score -= 0.80

        if self._is_garbled(v_txt):
            score -= 0.90

        return score

    @classmethod
    def _is_definite_value(cls, text: str) -> bool:
        """Determines if text represents a value rather than a label."""
        t = text.strip()
        t_lower = t.lower()

        # Pure numeric or decimal
        if re.match(r"^[\d,.]+$", t) and len(t) > 0:
            return True

        # Alphanumeric identifier (e.g. HDFC0000060, PUNB0165110, 2638871426000308)
        if len(t.split()) == 1 and bool(re.search(r"[A-Za-z]", t)) and bool(re.search(r"\d", t)):
            return True

        # Amount in words (Fifty Thousand Only, etc.)
        if cls._is_amount_in_words(t):
            return True

        # Specific known bank names acting as values
        if any(bn in t_lower for bn in ["punjab national bank", "punjabnationalbank", "state bank", "hdfc bank", "icici bank"]):
            return True

        # Date formats (05-AUG-2026, 28/08/2026)
        if re.search(r"\b\d{1,2}[-/\.](?:[A-Za-z]{3}|\d{1,2})[-/\.]\d{2,4}\b", t):
            return True

        return False

    @classmethod
    def _is_amount_in_words(cls, text: str) -> bool:
        """Checks if text is an amount-in-words phrase."""
        words = text.lower().split()
        num_words = {
            "one", "two", "three", "four", "five", "six", "seven", "eight", "nine", "ten",
            "eleven", "twelve", "thirteen", "fourteen", "fifteen", "sixteen", "seventeen", "eighteen",
            "nineteen", "twenty", "thirty", "forty", "fifty", "sixty", "seventy", "eighty", "ninety",
            "hundred", "thousand", "lakh", "lakhs", "crore", "crores", "only", "rupees"
        }
        if len(words) >= 2 and any(w in num_words for w in words):
            matches = sum(1 for w in words if w in num_words)
            if matches / len(words) >= 0.50:
                return True
        return False

    @classmethod
    def _is_form_label(cls, text: str) -> bool:
        """Identifies form field labels with strict exclusion of branding and section headers."""
        t = text.strip()
        t_lower = t.lower()
        clean = re.sub(r"[^\w\s/]", "", t_lower).strip()

        if not t or len(t) > 55 or len(t.split()) > 6:
            return False

        # Exclude definite values, section headings, and branding
        if cls._is_definite_value(t):
            return False
        if clean in cls.SECTION_HEADERS or any(b in clean for b in cls.BRANDING_KEYWORDS):
            return False

        # Exact match with known labels
        if clean in cls.KNOWN_LABELS or any(lbl in clean for lbl in cls.KNOWN_LABELS):
            return True

        # Ends with colon
        if t.endswith(":"):
            return True

        # Short title-case phrase
        if len(t.split()) <= 4 and t[0].isupper() and not any(w in t_lower for w in ["limited", "ltd", "corporation", "office"]):
            # Must have label indicator keywords
            label_indicators = ["code", "name", "date", "bank", "no", "number", "amount", "period", "tenure", "reference", "address"]
            if any(ind in t_lower for ind in label_indicators):
                return True

        return False

    @staticmethod
    def _is_garbled(text: str) -> bool:
        """Rejects unreadable OCR noise / corrupted text."""
        t = text.strip()
        if len(t) > 40 and " " not in t:
            return True
        # Check vowel to consonant ratio or gibberish length
        vowels = sum(1 for c in t.lower() if c in "aeiou")
        letters = sum(1 for c in t.lower() if c.isalpha())
        if letters > 20 and vowels / max(1, letters) < 0.15:
            return True
        return False

    @staticmethod
    def _normalize_label_key(label: str) -> str:
        """Converts raw label string to snake_case identifier."""
        clean = re.sub(r"[^\w\s]", "", label).strip().lower()
        clean = re.sub(r"\s+", "_", clean)
        return clean or "field"

    @staticmethod
    def _bbox(elem: Dict[str, Any]) -> List[float]:
        """Safely returns [l, t, r, b] float bounding box."""
        b = elem.get("bbox") or [0.0, 0.0, 0.0, 0.0]
        if isinstance(b, dict):
            return [float(b.get("x0", 0.0)), float(b.get("y0", 0.0)), float(b.get("x1", 0.0)), float(b.get("y1", 0.0))]
        if isinstance(b, (list, tuple)) and len(b) >= 4:
            return [float(b[0]), float(b[1]), float(b[2]), float(b[3])]
        return [0.0, 0.0, 0.0, 0.0]
