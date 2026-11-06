"""Quote checks that match the Paranoy server's normalization.

The server ignores case, whitespace, curly versus straight quotes, dash
styles, soft hyphens, and fi/fl ligatures. It does not allow a paraphrase
or words omitted inside the quotation.
"""

from __future__ import annotations

import re

_CHARS = {
    "\u2018": "'",
    "\u2019": "'",
    "\u201a": "'",
    "\u201b": "'",
    "\u201c": '"',
    "\u201d": '"',
    "\u201e": '"',
    "\u201f": '"',
    "\u00ab": '"',
    "\u00bb": '"',
    "\u2010": "-",
    "\u2011": "-",
    "\u2012": "-",
    "\u2013": "-",
    "\u2014": "-",
    "\u2015": "-",
    "\u2212": "-",
    "\u00ad": "",
    "\ufb01": "fi",
    "\ufb02": "fl",
    "\u00a0": " ",
    "\u2007": " ",
    "\u202f": " ",
}


def normalize_quote(text: str) -> str:
    """Fold the differences the server ignores. Order of other characters stays."""
    folded = []
    for char in str(text or ""):
        folded.append(_CHARS.get(char, char))
    compact = "".join(folded).casefold()
    return re.sub(r"\s+", " ", compact).strip()


def quote_in_evidence(quote: str, evidence: str) -> bool:
    """True when the quote survives as a substring after normalization."""
    needle = normalize_quote(quote)
    if not needle:
        return False
    return needle in normalize_quote(evidence)
