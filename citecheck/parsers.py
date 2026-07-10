"""Input parsers: turn files/strings into a list of :class:`Claim` objects.

Supported inputs:
  * BibTeX (.bib)                       -> parse_bibtex
  * LaTeX (.tex)                        -> extract \\cite keys (consistency check)
  * Markdown / prose reference lists    -> parse_reference_list
  * Loose identifiers / one-liners      -> parse_loose

The BibTeX parser is intentionally pragmatic (stdlib only): it handles the
common cases produced by reference managers, not every TeX edge case.
"""

from __future__ import annotations

import re
from typing import Dict, List, Optional, Tuple

from citecheck.models import Claim
from citecheck.sources import clean_doi, clean_arxiv


# ---------------------------------------------------------------------------
# Identifier detection (used across parsers)
# ---------------------------------------------------------------------------

DOI_RE = re.compile(r"\b10\.\d{4,9}/[-._;()/:A-Z0-9]+", re.I)
ARXIV_RE = re.compile(
    r"arxiv[:\s]*(\d{4}\.\d{4,5}(v\d+)?)"
    r"|arxiv[:\s]*([a-z\-]+/\d{7})"
    r"|\b(\d{4}\.\d{4,5})\b", re.I)
YEAR_RE = re.compile(r"\b(1[89]\d{2}|20\d{2})\b")


def find_doi(text: str) -> Optional[str]:
    m = DOI_RE.search(text or "")
    if not m:
        return None
    # Trim trailing punctuation that commonly clings to inline DOIs.
    return m.group(0).rstrip(".,;)")


def find_arxiv(text: str) -> Optional[str]:
    if not text:
        return None
    m = re.search(r"arxiv[:\s]*([a-z\-]+/\d{7}|\d{4}\.\d{4,5})(v\d+)?", text, re.I)
    if m:
        return clean_arxiv(m.group(1))
    return None


# ---------------------------------------------------------------------------
# BibTeX
# ---------------------------------------------------------------------------

def _split_bibtex_entries(text: str) -> List[Tuple[str, str, str]]:
    """Yield (entry_type, key, body) for each @type{key, ...} block."""
    entries = []
    i = 0
    n = len(text)
    while i < n:
        at = text.find("@", i)
        if at == -1:
            break
        m = re.match(r"@(\w+)\s*[{(]", text[at:])
        if not m:
            i = at + 1
            continue
        etype = m.group(1).lower()
        if etype in ("comment", "preamble", "string"):
            i = at + m.end()
            continue
        # find matching closing brace
        brace_start = at + m.end() - 1
        open_ch = text[brace_start]
        close_ch = "}" if open_ch == "{" else ")"
        depth = 0
        j = brace_start
        while j < n:
            if text[j] == open_ch:
                depth += 1
            elif text[j] == close_ch:
                depth -= 1
                if depth == 0:
                    break
            j += 1
        inner = text[brace_start + 1:j]
        key_match = re.match(r"\s*([^,\s]+)\s*,", inner)
        key = key_match.group(1) if key_match else ""
        body = inner[key_match.end():] if key_match else inner
        entries.append((etype, key, body))
        i = j + 1
    return entries


def _parse_bibtex_fields(body: str) -> Dict[str, str]:
    """Parse `field = {value}` / `field = "value"` / `field = value` pairs."""
    fields: Dict[str, str] = {}
    i, n = 0, len(body)
    while i < n:
        m = re.match(r"\s*(\w[\w\-]*)\s*=\s*", body[i:])
        if not m:
            i += 1
            continue
        name = m.group(1).lower()
        i += m.end()
        if i >= n:
            break
        ch = body[i]
        if ch == "{":
            depth, j = 0, i
            while j < n:
                if body[j] == "{":
                    depth += 1
                elif body[j] == "}":
                    depth -= 1
                    if depth == 0:
                        break
                j += 1
            value = body[i + 1:j]
            i = j + 1
        elif ch == '"':
            j = i + 1
            while j < n and body[j] != '"':
                j += 1
            value = body[i + 1:j]
            i = j + 1
        else:
            j = i
            while j < n and body[j] not in ",\n":
                j += 1
            value = body[i:j]
            i = j
        fields[name] = _clean_bibtex_value(value)
        # advance past the trailing comma
        while i < n and body[i] in ", \n\t":
            i += 1
    return fields


def _clean_bibtex_value(value: str) -> str:
    value = value.replace("\n", " ").replace("\t", " ")
    value = re.sub(r"\{\\[a-zA-Z]+\s*\{([^}]*)\}\}", r"\1", value)  # {\"{o}} -> o-ish
    value = value.replace("{", "").replace("}", "")
    value = re.sub(r"\\[a-zA-Z]+", "", value)   # strip remaining \commands
    value = value.replace("\\", "")
    return " ".join(value.split()).strip()


def _bibtex_authors(raw: str) -> List[str]:
    if not raw:
        return []
    return [a.strip() for a in re.split(r"\s+and\s+", raw) if a.strip()]


def parse_bibtex(text: str) -> List[Claim]:
    claims = []
    for etype, key, body in _split_bibtex_entries(text):
        f = _parse_bibtex_fields(body)
        year = None
        if f.get("year"):
            ym = YEAR_RE.search(f["year"])
            year = int(ym.group(1)) if ym else None
        venue = f.get("journal") or f.get("booktitle") or f.get("publisher") \
            or f.get("school") or f.get("institution")
        # Identifiers are often mislabeled — a DOI in `url`, an arXiv id stuffed
        # into `journal` ("arXiv preprint arXiv:2310.01063") or `note`. Prefer the
        # dedicated fields, then fall back to scanning every field value so a
        # buried-but-real identifier still gets cross-checked (this is exactly
        # how a fabricated author list on a real arXiv id gets caught).
        all_values = " ".join(f.values())
        doi = (clean_doi(f.get("doi")) or find_doi(f.get("url", ""))
               or find_doi(f.get("note", "")) or find_doi(all_values))
        arxiv = clean_arxiv(f.get("eprint")) \
            if f.get("archiveprefix", "").lower() == "arxiv" else None
        if not arxiv:
            arxiv = (find_arxiv(f.get("eprint", "")) or find_arxiv(f.get("note", ""))
                     or find_arxiv(f.get("url", "")) or find_arxiv(f.get("journal", ""))
                     or find_arxiv(all_values))
        claims.append(Claim(
            key=key, raw=f"@{etype}{{{key}}}", title=f.get("title"),
            authors=_bibtex_authors(f.get("author", "")), year=year,
            venue=venue, doi=doi, arxiv_id=arxiv, url=f.get("url"),
            entry_type=etype,
        ))
    return claims


def extract_cite_keys(tex_text: str) -> List[str]:
    """All keys referenced by \\cite-family commands in a LaTeX document."""
    keys: List[str] = []
    pattern = re.compile(r"\\(?:cite|citep|citet|citeauthor|citeyear|"
                         r"parencite|textcite|autocite|footcite)\*?"
                         r"(?:\[[^\]]*\])*\{([^}]+)\}")
    for m in pattern.finditer(tex_text):
        for k in m.group(1).split(","):
            k = k.strip()
            if k:
                keys.append(k)
    # de-dup, preserve order
    seen = set()
    out = []
    for k in keys:
        if k not in seen:
            seen.add(k)
            out.append(k)
    return out


# ---------------------------------------------------------------------------
# LaTeX \bibitem / thebibliography (embedded bibliographies)
# ---------------------------------------------------------------------------

import unicodedata as _unicodedata

_THEBIB_RE = re.compile(
    r"\\begin\{thebibliography\}.*?\n(.*?)\\end\{thebibliography\}", re.S)


def clean_latex(s: str) -> str:
    """Best-effort conversion of a LaTeX reference string to plain text.

    Resolves accent commands (\\\"{u} -> u, \\'{o} -> o), unwraps \\emph{...} and
    similar, handles \\& and ~, strips remaining braces/commands, and drops any
    combining marks left behind.
    """
    if not s:
        return ""
    # Accent commands with a braced/plain argument: \"{u}, \'o, \c{c}
    s = re.sub(r"\\[\"'`^~=.uvHck]\s*\{?\\?([a-zA-Z])\}?", r"\1", s)
    s = re.sub(r"\\[a-zA-Z]+\s*\{([^{}]*)\}", r"\1", s)   # \emph{x} -> x
    s = s.replace(r"\&", "&").replace(r"\%", "%").replace(r"\$", "$")
    s = s.replace("~", " ").replace("--", "-")
    s = s.replace("{", "").replace("}", "")
    s = re.sub(r"\\[a-zA-Z]+", "", s)                     # leftover \commands
    s = s.replace("\\", "")
    s = "".join(c for c in _unicodedata.normalize("NFKD", s)
                if not _unicodedata.combining(c))
    return " ".join(s.split())


def is_thebibliography(text: str) -> bool:
    return "\\bibitem" in text or "\\begin{thebibliography}" in text


def parse_thebibliography(text: str) -> List[Claim]:
    """Parse a LaTeX ``thebibliography`` block, preserving real \\bibitem keys.

    Each ``\\bibitem[label]{key}`` is followed by a free-form reference string;
    the reference text is LaTeX-cleaned and parsed with the same year-anchored
    heuristics used for prose bibliographies.
    """
    m = _THEBIB_RE.search(text)
    block = m.group(1) if m else text
    claims: List[Claim] = []
    # Split on \bibitem; the first chunk (preamble) has no key and is skipped.
    for idx, part in enumerate(re.split(r"\\bibitem", block)):
        part = part.strip()
        if not part:
            continue
        km = re.match(r"(?:\[[^\]]*\])?\s*\{([^}]+)\}", part)
        if not km:
            continue
        key = km.group(1).strip()
        ref = clean_latex(part[km.end():].strip())
        if not ref:
            continue
        ym = YEAR_RE.search(ref)
        claims.append(Claim(
            key=key, raw=ref, title=_guess_title(ref),
            authors=_guess_authors(ref),
            year=int(ym.group(1)) if ym else None,
            doi=find_doi(ref), arxiv_id=find_arxiv(ref),
        ))
    return claims


# ---------------------------------------------------------------------------
# Prose / Markdown reference lists
# ---------------------------------------------------------------------------

def _split_reference_list(text: str) -> List[str]:
    """Split a References section into individual entries.

    Recognizes numbered ([1], 1., (1)) and blank-line-separated entries; falls
    back to line-per-entry for hanging-indent APA/MLA lists.
    """
    # Isolate a References/Bibliography section if present.
    m = re.search(r"(?im)^\s*#*\s*(references|bibliography|works cited|"
                  r"literature cited)\s*:?\s*$", text)
    if m:
        text = text[m.end():]

    lines = text.splitlines()
    # Numbered entries?
    num_re = re.compile(r"^\s*(?:\[\d+\]|\(\d+\)|\d+[.)])\s+")
    if sum(1 for ln in lines if num_re.match(ln)) >= 2:
        entries, cur = [], []
        for ln in lines:
            if num_re.match(ln):
                if cur:
                    entries.append(" ".join(cur).strip())
                cur = [num_re.sub("", ln).strip()]
            elif ln.strip() and cur:
                cur.append(ln.strip())
        if cur:
            entries.append(" ".join(cur).strip())
        return [e for e in entries if e]

    # Blank-line-separated blocks?
    blocks = re.split(r"\n\s*\n", text)
    blocks = [" ".join(b.split()) for b in blocks if b.strip()]
    if len(blocks) >= 2:
        return blocks

    # One entry per non-empty line.
    return [ln.strip() for ln in lines if ln.strip()]


# The publication year that separates the author list from the title. Matches
# a parenthesized year — "(2015)", "(2020a)" — OR a bare year followed by a
# period/comma — "..., 2024. Title" — which is the dominant style in embedded
# \bibitem bibliographies. A negative lookbehind avoids matching inside a
# larger number (e.g. a volume or page range).
_YEAR_ANCHOR_RE = re.compile(
    r"\(\s*(1[89]\d{2}|20\d{2})[a-z]?\s*\)"
    r"|(?<!\d)(1[89]\d{2}|20\d{2})[a-z]?(?=\s*[.,])")


def _year_anchor(entry: str):
    """Return the match object for the year that delimits authors from title."""
    return _YEAR_ANCHOR_RE.search(entry or "")


def _guess_title(entry: str) -> Optional[str]:
    """Heuristically pull a title out of a free-form reference string."""
    # Quoted title (most reliable).
    m = re.search(r"[\"“]([^\"”]{6,})[\"”]", entry)
    if m:
        return m.group(1).strip().rstrip(".")
    # Year-anchored: everything after the publication year, up to the sentence
    # break that precedes the venue. Handles both "(year). Title." and
    # "authors, year. Title." forms.
    ya = _year_anchor(entry)
    if ya:
        after = entry[ya.end():].lstrip(" .,)")
        title = re.split(r"\.\s", after, 1)[0].strip().rstrip(".")
        if len(title) >= 6:
            return title
    # Fallback: the first sentence-like chunk that isn't an author list.
    chunks = [c.strip() for c in entry.split(".") if len(c.strip()) > 15]
    for c in chunks:
        if not re.match(r"^,?\s*[A-Z][\w'’-]+,\s*[A-Z]\.", c):
            return c
    return chunks[0] if chunks else None


def _guess_authors(entry: str) -> List[str]:
    """Author list is usually the text before the publication year."""
    ya = _year_anchor(entry)
    head = entry[:ya.start()] if ya else entry[:120]
    head = head.strip().rstrip("(,. ")
    if not head:
        return []
    # Split "A, B, & C" / "A; B; C" / "A and B"
    head = re.sub(r"\s*&\s*", ", ", head)
    head = re.sub(r"\s+and\s+", ", ", head)
    parts = re.split(r";|,(?=\s*[A-Z])", head)
    authors, buf = [], []
    # Recombine "Surname, X." pairs that got split on the comma.
    tokens = [p.strip() for p in re.split(r";", head)] if ";" in head else None
    if tokens:
        return [t for t in tokens if t]
    # Comma-heuristic: treat pairs as Surname, Initials
    raw = [p.strip() for p in head.split(",") if p.strip()]
    i = 0
    while i < len(raw):
        if i + 1 < len(raw) and re.match(r"^([A-Z]\.?\s*){1,4}$", raw[i + 1]):
            authors.append(f"{raw[i]}, {raw[i+1]}")
            i += 2
        else:
            authors.append(raw[i])
            i += 1
    return authors[:20]


def parse_reference_list(text: str) -> List[Claim]:
    claims = []
    for idx, entry in enumerate(_split_reference_list(text), 1):
        ym = YEAR_RE.search(entry)
        year = int(ym.group(1)) if ym else None
        claims.append(Claim(
            key=f"ref-{idx}", raw=entry, title=_guess_title(entry),
            authors=_guess_authors(entry), year=year,
            doi=find_doi(entry), arxiv_id=find_arxiv(entry),
        ))
    return claims


# ---------------------------------------------------------------------------
# Loose identifiers / single-citation strings
# ---------------------------------------------------------------------------

def parse_loose(text: str) -> List[Claim]:
    """Parse a raw list: one DOI/arXiv id/title per line, or a single citation."""
    claims = []
    for idx, line in enumerate((ln.strip() for ln in text.splitlines()), 1):
        if not line or line.startswith("#"):
            continue
        doi = find_doi(line)
        arxiv = find_arxiv(line)
        # If the line is *just* an identifier, leave title empty (pure existence).
        stripped = line
        is_bare_doi = doi and stripped.rstrip(".,;") in (doi, f"doi:{doi}",
                                                         f"https://doi.org/{doi}")
        is_bare_arxiv = arxiv and re.fullmatch(
            r"(arxiv:\s*)?[a-z\-]*/?\d{4}\.?\d{3,7}(v\d+)?", stripped, re.I)
        if is_bare_doi or is_bare_arxiv:
            claims.append(Claim(key=f"id-{idx}", raw=line, doi=doi, arxiv_id=arxiv))
            continue
        ym = YEAR_RE.search(line)
        claims.append(Claim(
            key=f"cite-{idx}", raw=line, title=_guess_title(line) or line,
            authors=_guess_authors(line),
            year=int(ym.group(1)) if ym else None,
            doi=doi, arxiv_id=arxiv,
        ))
    return claims


# ---------------------------------------------------------------------------
# Format autodetection
# ---------------------------------------------------------------------------

def detect_and_parse(text: str, fmt: str = "auto",
                     filename: str = "") -> List[Claim]:
    fmt = (fmt or "auto").lower()
    if fmt == "auto":
        low = filename.lower()
        if is_thebibliography(text):
            fmt = "latex"                       # embedded \bibitem bibliography
        elif low.endswith(".bib") or re.search(r"@\w+\s*\{", text):
            fmt = "bibtex"
        elif low.endswith(".tex"):
            fmt = "bibtex"  # a .tex alone has no entries; handled via consistency
        elif re.search(r"(?im)^\s*#*\s*(references|bibliography|works cited)\s*:?\s*$",
                       text):
            fmt = "prose"
        elif low.endswith((".md", ".txt")):
            fmt = "prose"
        else:
            fmt = "loose"
    if fmt == "latex":
        claims = parse_thebibliography(text)
        if claims:
            return claims
        return parse_reference_list(text)
    if fmt == "bibtex":
        claims = parse_bibtex(text)
        if claims:
            return claims
        return parse_loose(text)
    if fmt == "prose":
        return parse_reference_list(text)
    if fmt == "loose":
        return parse_loose(text)
    raise ValueError(f"unknown format: {fmt}")
