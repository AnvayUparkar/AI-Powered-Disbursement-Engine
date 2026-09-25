"""Confusable character set and lexicon tie-break signal for comb-box fields.

Generates suggested candidate corrections for characters at confidence-outlier
positions when a known optical confusable pair matches a real name or dictionary term.
Suggestions are attached as metadata for human review or VLM inspection, never auto-applied.
"""
from typing import Dict, List, Optional, Set, Tuple


CONFUSABLE_PAIRS: Dict[str, List[str]] = {
    "K": ["R"], "R": ["K"],
    "O": ["0"], "0": ["O"],
    "I": ["1", "L"], "1": ["I"], "L": ["1"],
    "S": ["5"], "5": ["S"],
    "U": ["V"], "V": ["U"],
    "B": ["8"], "8": ["B"],
    "D": ["0", "O"],
    "Z": ["2"], "2": ["Z"],
    "G": ["6"], "6": ["G"],
    "T": ["7"], "7": ["T"],
}

# Lexicon of common Indian names, banking labels, and form terms
COMMON_LEXICON: Set[str] = {
    "MARKET", "AKSHALI", "MOHAN", "KUMAR", "SINGH", "SHARMA", "VERMA", "PATEL",
    "GUPTA", "REDDY", "RAO", "NAIR", "PILLAI", "MENON", "IYER", "IYENGAR",
    "DESHMUKH", "JADHAV", "CHAVAN", "PAWAR", "SHINDE", "KULKARNI", "JOSHI",
    "KAPOOR", "KHANNA", "MALHOTRA", "CHOPRA", "MEHTA", "SHAH", "CHOUDHARY",
    "YADAV", "MISHRA", "PANDEY", "TIWARI", "TRIPATHI", "BHARADWAJ", "DUBEY",
    "AGARWAL", "BANSAL", "MITTAL", "GOEL", "GARG", "JINDAL", "SINGHAL",
    "SAVINGS", "CURRENT", "SALARY", "LOAN", "BRANCH", "ACCOUNT", "HOLDER",
    "APPLICANT", "RESIDENCE", "PERMANENT", "OFFICE", "BUSINESS", "EMPLOYER",
    "STREET", "ROAD", "NAGAR", "COLONY", "ENCLAVE", "SECTOR", "PHASE",
    "MUMBAI", "DELHI", "BANGALORE", "HYDERABAD", "CHENNAI", "KOLKATA",
    "PUNE", "AHMEDABAD", "JAIPUR", "SURAT", "LUCKNOW", "KANPUR", "NAGPUR",
}


def suggest_confusable_corrections(
    text: str,
    outlier_positions: List[int],
    custom_lexicon: Optional[Set[str]] = None,
) -> Optional[Dict[str, str]]:
    """Inspect outlier positions in text against confusable character pairs.

    If substituting a confusable glyph produces a recognized lexicon term
    while the current string does not match, returns a suggestion dictionary.
    """
    if not text or not outlier_positions:
        return None

    lexicon = (COMMON_LEXICON | custom_lexicon) if custom_lexicon else COMMON_LEXICON
    clean_upper = text.strip().upper()
    if clean_upper in lexicon:
        return None

    chars = list(clean_upper)
    for pos in outlier_positions:
        if pos < 0 or pos >= len(chars):
            continue
        curr_char = chars[pos]
        alternatives = CONFUSABLE_PAIRS.get(curr_char, [])
        for alt in alternatives:
            candidate_chars = list(chars)
            candidate_chars[pos] = alt
            candidate_str = "".join(candidate_chars)
            if candidate_str in lexicon:
                return {
                    "original_text": text,
                    "suggested_text": candidate_str,
                    "position": pos,
                    "original_char": curr_char,
                    "suggested_char": alt,
                    "rationale": "confusable_char_lexicon_hit",
                }

    return None
