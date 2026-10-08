"""Text normalisation shared by resolution, citekeys, rendering and quote checks."""
from __future__ import annotations

import math
import re
import unicodedata

from rapidfuzz import fuzz

STOPWORDS = {
    "a", "an", "the", "on", "of", "in", "for", "to", "and", "with", "towards", "toward", "via", "is", "are", "your",
    "all", "you", "we", "how", "what", "why", "do", "does", "can", "at", "by", "from", "into", "as", "be", "not",
    "when", "where", "which", "its", "it", "our", "using", "beyond", "about", "rethinking", "revisiting", "i",
}

_LATEX_CMD = re.compile(r"\\[a-zA-Z]+\*?\s*")
_NON_WORD = re.compile(r"[^\w\s]", re.UNICODE)
_WS = re.compile(r"\s+")


def strip_accents(text: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFKD", text) if not unicodedata.combining(c))


def ascii_fold(text: str) -> str:
    folded = strip_accents(text)
    folded = folded.replace("ß", "ss").replace("ø", "o").replace("Ø", "O").replace("ł", "l").replace("Ł", "L") \
        .replace("æ", "ae").replace("Æ", "AE").replace("œ", "oe").replace("đ", "d").replace("ı", "i")
    return folded.encode("ascii", "ignore").decode()


def norm_title(title: str) -> str:
    text = _LATEX_CMD.sub(" ", title or "")
    text = text.replace("{", "").replace("}", "").replace("$", "")
    text = strip_accents(text).casefold()
    text = _NON_WORD.sub(" ", text)
    return _WS.sub(" ", text).strip()


def alias_key(name: str) -> str:
    """Loose identity of a name for matching entities and leaderboard groups: 'S-NIAH', 'SNIAH', 's niah' -> 'sniah'."""
    return norm_title(name).replace(" ", "").replace("_", "")


def title_similarity(a: str, b: str) -> float:
    na, nb = norm_title(a), norm_title(b)
    if not na or not nb:
        return 0.0
    # token_set_ratio rewards subsets ("BERT" vs "BERT: Pre-training ..."); guard with a length-aware ratio.
    return min(fuzz.token_set_ratio(na, nb), fuzz.ratio(na, nb) + 15) / 100.0


def family_name(author: dict) -> str:
    if not isinstance(author, dict):
        return ""
    if author.get("family"):
        return author["family"].strip()
    literal = (author.get("literal") or author.get("name") or "").strip()
    if "," in literal:
        return literal.split(",")[0].strip()
    return literal.split()[-1] if literal.split() else ""


def norm_surname(name: str) -> str:
    return re.sub(r"[^a-z]", "", ascii_fold(name).lower())


def short_authors(authors: list) -> str:
    if not authors:
        return ""
    first = family_name(authors[0])
    return first if len(authors) == 1 else f"{first}+{len(authors) - 1}"


def first_significant_word(title: str) -> str:
    for word in norm_title(ascii_fold(title)).split():
        if word not in STOPWORDS and word.isalnum():
            return word
    words = norm_title(ascii_fold(title)).split()
    return words[0] if words else "untitled"


_VENUES = [
    (r"neural information processing systems|\bneurips\b|\bnips\b", "NeurIPS"),
    (r"international conference on machine learning|\bicml\b", "ICML"),
    (r"international conference on learning representations|\biclr\b", "ICLR"),
    (r"findings of the association for computational linguistics|findings of acl", "Findings"),
    (r"empirical methods in natural language processing|\bemnlp\b", "EMNLP"),
    (r"north american chapter|\bnaacl\b", "NAACL"),
    (r"european chapter of the association|\beacl\b", "EACL"),
    (r"transactions of the association for computational linguistics|\btacl\b", "TACL"),
    (r"association for computational linguistics|\bacl\b", "ACL"),
    (r"computer vision and pattern recognition|\bcvpr\b", "CVPR"),
    (r"international conference on computer vision|\biccv\b", "ICCV"),
    (r"european conference on computer vision|\beccv\b", "ECCV"),
    (r"aaai conference|\baaai\b", "AAAI"),
    (r"international joint conference on artificial intelligence|\bijcai\b", "IJCAI"),
    (r"conference on language modeling|\bcolm\b", "COLM"),
    (r"transactions on machine learning research|\btmlr\b", "TMLR"),
    (r"journal of machine learning research|\bjmlr\b", "JMLR"),
    (r"artificial intelligence and statistics|\baistats\b", "AISTATS"),
    (r"uncertainty in artificial intelligence|\buai\b", "UAI"),
    (r"conference on robot learning|\bcorl\b", "CoRL"),
    (r"knowledge discovery and data mining|\bkdd\b", "KDD"),
    (r"\bnature\b(?! (communications|machine|neuroscience|methods))", "Nature"),
    (r"nature communications", "Nat. Commun."),
    (r"nature machine intelligence", "Nat. Mach. Intell."),
    (r"nature neuroscience", "Nat. Neurosci."),
    (r"\bscience\b(?! (advances|robotics))", "Science"),
    (r"proceedings of the national academy|\bpnas\b", "PNAS"),
    (r"neural computation", "Neural Comput."),
    (r"\bneuron\b", "Neuron"),
    (r"physical review letters", "PRL"),
    (r"\barxiv\b|corr\b", "arXiv"),
]


def venue_abbrev(container: str, year: int | None = None) -> str:
    if not container:
        return ""
    low = container.lower()
    short = next((abbr for pattern, abbr in _VENUES if re.search(pattern, low)), "")
    if not short:
        short = container if len(container) <= 24 else container[:22].rstrip() + "…"
    return f"{short}'{str(year)[-2:]}" if year else short


def est_tokens(text: str) -> int:
    """Offline token estimate (≈3.4 chars/token for citekey/ID-heavy text). Errs on the high side."""
    return math.ceil(len(text) / 3.4)


_LIGATURES = {"ﬁ": "fi", "ﬂ": "fl", "ﬀ": "ff", "ﬃ": "ffi", "ﬄ": "ffl", "ﬅ": "st", "ﬆ": "st"}
_QUOTES = {"“": '"', "”": '"', "„": '"', "‘": "'", "’": "'", "‚": "'", "´": "'", "`": "'",
           "–": "-", "—": "-", "‐": "-", "‑": "-", "−": "-", "\u00a0": " ", "\u2009": " ", "\u202f": " "}


def norm_quote(text: str) -> str:
    """Normalise for quote matching: ligatures, dashes/quotes, line-break hyphenation, whitespace, case."""
    for a, b in {**_LIGATURES, **_QUOTES}.items():
        text = text.replace(a, b)
    text = unicodedata.normalize("NFKC", text)
    text = re.sub(r"(\w)-\s*\n\s*(\w)", r"\1\2", text)
    text = re.sub(r"\[\d+(?:[,\u2013\-]\s*\d+)*\]", " ", text)  # numeric citation markers
    return _WS.sub(" ", text).strip().casefold()


def slug(text: str, keep: str = "", limit: int = 60, default: str = "") -> str:
    """Lowercase a-z0-9 (plus the characters in `keep`); every other run of characters becomes one '-'."""
    trim = "-" + keep
    s = re.sub(rf"[^a-z0-9{re.escape(keep)}]+", "-", (text or "").strip().lower()).strip(trim)
    return s[:limit].rstrip(trim) or default


def title_slug(title: str) -> str:
    """An id-friendly form of a title: 'C++ Templates' -> 'c-plus-plus-templates'."""
    return slug(norm_title((title or "").replace("++", " plus plus ").replace("+", " plus ").replace("#", " sharp ")))


def clip(text: str, limit: int) -> str:
    text = _WS.sub(" ", text or "").strip()
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"
