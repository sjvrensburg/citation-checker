"""Field normalization, comparison, and the verdict decision logic.

This module is the substance of citecheck: given what a citation *claims* and
what a canonical source *returned*, it decides whether they describe the same
work and whether every asserted detail (authors, year, venue) holds up.

The rules deliberately treat different failure modes as distinct categories,
because "the DOI points to a different paper" is a categorically worse problem
than "the year is off by one".
"""

from __future__ import annotations

import re
import unicodedata
from difflib import SequenceMatcher
from typing import List, Optional, Tuple

from citecheck.models import (
    Claim, Record, FieldCheck, Verdict,
    VERIFIED, MINOR_MISMATCH, METADATA_MISMATCH, DOI_MISMATCH, NOT_FOUND,
)

# ---------------------------------------------------------------------------
# Thresholds (overridable via Thresholds instances passed to decide()).
# ---------------------------------------------------------------------------


class Thresholds:
    # Title similarity above which two titles are "the same work".
    title_same = 0.82
    # Title similarity below which an identifier clearly points elsewhere.
    title_different = 0.55
    # Fraction of claimed author surnames that must appear in the record.
    author_overlap = 0.6
    # Year tolerance (preprint vs published, in-press drift).
    year_tolerance = 1


STRICT = Thresholds()
STRICT.title_same = 0.9
STRICT.title_different = 0.6
STRICT.author_overlap = 0.75
STRICT.year_tolerance = 0

LENIENT = Thresholds()
LENIENT.title_same = 0.75
LENIENT.title_different = 0.45
LENIENT.author_overlap = 0.5
LENIENT.year_tolerance = 1


# ---------------------------------------------------------------------------
# Normalization primitives
# ---------------------------------------------------------------------------

def strip_accents(text: str) -> str:
    nfkd = unicodedata.normalize("NFKD", text)
    return "".join(c for c in nfkd if not unicodedata.combining(c))


def norm_text(text: Optional[str]) -> str:
    """Lowercase, de-accent, strip punctuation, collapse whitespace."""
    if not text:
        return ""
    text = strip_accents(text.lower())
    text = re.sub(r"[^\w\s]", " ", text)
    return " ".join(text.split())


def title_similarity(a: Optional[str], b: Optional[str]) -> float:
    na, nb = norm_text(a), norm_text(b)
    if not na or not nb:
        return 0.0
    if na == nb:
        return 1.0
    return SequenceMatcher(None, na, nb).ratio()


def surname(name: str) -> str:
    """Extract a comparable surname token from many author-name formats.

    Handles "Vaswani, Ashish", "Ashish Vaswani", "A. Vaswani", "van der Berg, J.".
    """
    name = strip_accents(name).strip()
    if not name:
        return ""
    if "," in name:
        last = name.split(",", 1)[0]
    else:
        # surname is the trailing token, but keep nobiliary particles attached
        tokens = name.split()
        if not tokens:
            return ""
        last = tokens[-1]
        # pull in preceding nobiliary/compound particles:
        # "van der Berg" -> "van der berg", "Davide Delle Monache" -> "delle monache"
        particles = {
            "van", "von", "vander", "der", "den", "ter", "ten", "te",
            "de", "del", "della", "delle", "dell", "degli", "dei", "di",
            "da", "dos", "das", "du", "la", "le", "las", "los", "lo",
            "af", "av", "bin", "ibn", "al", "el", "abu", "mac", "mc",
            "san", "santa", "saint", "st", "ould",
        }
        i = len(tokens) - 2
        parts = [last]
        while i >= 0 and tokens[i].lower() in particles:
            parts.insert(0, tokens[i])
            i -= 1
        last = " ".join(parts)
    last = re.sub(r"[^\w\s]", "", last.lower())
    return " ".join(last.split())


def surnames(authors: List[str]) -> List[str]:
    out = []
    for a in authors:
        s = surname(a)
        if s:
            out.append(s)
    return out


def venue_similarity(a: Optional[str], b: Optional[str]) -> float:
    """Venue names vary wildly (abbreviations, "Proc. of ..."). Compare loosely:
    exact-ish match OR one being a subsequence/abbreviation of the other."""
    na, nb = norm_text(a), norm_text(b)
    if not na or not nb:
        return 0.0
    if na == nb:
        return 1.0
    ratio = SequenceMatcher(None, na, nb).ratio()
    # Acronym / containment heuristics for things like "NeurIPS" vs
    # "Advances in Neural Information Processing Systems".
    if na in nb or nb in na:
        ratio = max(ratio, 0.85)
    return ratio


# ---------------------------------------------------------------------------
# Field-level checks
# ---------------------------------------------------------------------------

def check_title(claim: Claim, rec: Record) -> Optional[FieldCheck]:
    if not claim.title or not rec.title:
        return None
    sim = title_similarity(claim.title, rec.title)
    ok = sim >= 0.82
    sev = "info" if ok else ("major" if sim < 0.55 else "minor")
    return FieldCheck("title", claim.title, rec.title, sim, ok, sev,
                      "" if ok else "titles differ")


def check_first_author(claim: Claim, rec: Record, th: Thresholds) -> Optional[FieldCheck]:
    if not claim.authors or not rec.authors:
        return None
    c_first = surname(claim.authors[0])
    r_surs = surnames(rec.authors)
    if not c_first or not r_surs:
        return None
    r_first = r_surs[0]
    ok = c_first == r_first or c_first in r_surs
    sim = 1.0 if c_first == r_first else (0.7 if c_first in r_surs else 0.0)
    if ok and c_first != r_first:
        note = f"claimed first author '{c_first}' appears but not first (record leads with '{r_first}')"
        sev = "minor"
    elif ok:
        note = ""
        sev = "info"
    else:
        note = f"first author '{c_first}' not among record authors {r_surs[:5]}"
        sev = "major"
    return FieldCheck("first_author", claim.authors[0],
                      rec.authors[0] if rec.authors else None, sim, ok, sev, note)


def check_authors(claim: Claim, rec: Record, th: Thresholds) -> Optional[FieldCheck]:
    if not claim.authors or not rec.authors:
        return None
    c = set(surnames(claim.authors))
    r = set(surnames(rec.authors))
    if not c or not r:
        return None
    overlap = len(c & r) / len(c)   # fraction of *claimed* authors found in record
    ok = overlap >= th.author_overlap
    missing = sorted(c - r)
    sev = "info" if ok else ("major" if overlap < 0.3 else "minor")
    note = "" if ok else f"claimed authors not in record: {missing}"
    return FieldCheck("authors", sorted(c), sorted(r), overlap, ok, sev, note)


def check_year(claim: Claim, rec: Record, th: Thresholds) -> Optional[FieldCheck]:
    if not claim.year or not rec.year:
        return None
    diff = abs(int(claim.year) - int(rec.year))
    ok = diff <= th.year_tolerance
    sim = 1.0 if diff == 0 else max(0.0, 1.0 - diff / 5.0)
    # A 1-year gap is normal preprint/in-press drift: never a hard error, only
    # minor (and fully OK under lenient tolerance). Larger gaps are major.
    if diff == 0:
        sev, note = "info", ""
    elif diff == 1:
        sev, note = "minor", "year off by 1 (preprint/in-press drift)"
    else:
        sev, note = "major", f"year off by {diff}"
    return FieldCheck("year", claim.year, rec.year, sim, ok, sev, note)


def check_venue(claim: Claim, rec: Record) -> Optional[FieldCheck]:
    if not claim.venue or not rec.venue:
        return None
    sim = venue_similarity(claim.venue, rec.venue)
    ok = sim >= 0.6
    sev = "info" if ok else "minor"   # venue mismatches are rarely "critical" alone
    return FieldCheck("venue", claim.venue, rec.venue, sim, ok, sev,
                      "" if ok else "venue differs")


# ---------------------------------------------------------------------------
# Verdict decision
# ---------------------------------------------------------------------------

def _confidence(checks: List[FieldCheck]) -> float:
    if not checks:
        return 0.0
    weights = {"title": 0.4, "first_author": 0.2, "authors": 0.15,
               "year": 0.15, "venue": 0.1}
    total_w = 0.0
    score = 0.0
    for c in checks:
        w = weights.get(c.field, 0.05)
        total_w += w
        score += w * c.similarity
    return score / total_w if total_w else 0.0


def build_checks(claim: Claim, rec: Record, th: Thresholds) -> List[FieldCheck]:
    checks: List[FieldCheck] = []
    for c in (
        check_title(claim, rec),
        check_first_author(claim, rec, th),
        check_authors(claim, rec, th),
        check_year(claim, rec, th),
        check_venue(claim, rec),
    ):
        if c is not None:
            checks.append(c)
    return checks


def decide(claim: Claim, rec: Record, th: Thresholds = STRICT) -> Verdict:
    """Given a claim and the record chosen as its best match, produce a verdict.

    The record's ``matched_by`` field determines how strict identity is judged:
    an identifier match (doi/arxiv) triggers the "valid ID, wrong paper" check.
    """
    checks = build_checks(claim, rec, th)
    conf = _confidence(checks)
    title_check = next((c for c in checks if c.field == "title"), None)
    first_author_check = next((c for c in checks if c.field == "first_author"), None)
    authors_check = next((c for c in checks if c.field == "authors"), None)
    year_check = next((c for c in checks if c.field == "year"), None)
    venue_check = next((c for c in checks if c.field == "venue"), None)
    by_identifier = rec.matched_by in ("doi", "arxiv")

    messages: List[str] = []

    # --- Identifier integrity: does the DOI/arXiv point to the claimed paper? ---
    if by_identifier and title_check is not None and \
            title_check.similarity < th.title_different:
        # A low title similarity alone is ambiguous: publishers sometimes
        # register a short/abbreviated title. If the author, year, and venue all
        # corroborate, the identifier points to the SAME work and the *title* is
        # simply wrong/embellished — a metadata error, not a different paper.
        corroborators = [c for c in (first_author_check, year_check, venue_check)
                         if c is not None]
        corroborated = (first_author_check is not None and first_author_check.ok
                        and (year_check is None or year_check.ok)
                        and len(corroborators) >= 2
                        and all(c.ok for c in corroborators))
        if corroborated:
            messages.append(
                f"The {rec.matched_by.upper()} points to the correct work "
                f"(author, year, and venue match), but the cited TITLE does not "
                f"match the registered title: claimed “{claim.title}” vs. "
                f"registered “{rec.title}”. The title appears wrong or embellished."
            )
            return Verdict(claim, METADATA_MISMATCH, conf, rec, checks, messages)
        messages.append(
            f"The {rec.matched_by.upper()} resolves, but to a DIFFERENT paper: "
            f"claimed “{claim.title}” vs. actual “{rec.title}”."
        )
        return Verdict(claim, DOI_MISMATCH, conf, rec, checks, messages)

    # If matched only by title search, require the titles to actually be the same work.
    if rec.matched_by == "title-search" and title_check is not None:
        if title_check.similarity < th.title_same:
            messages.append(
                f"Best title-search candidate is not a confident match "
                f"(similarity {title_check.similarity:.2f}); treat as unverified."
            )
            v = Verdict(claim, NOT_FOUND, conf, None, checks, messages)
            v.considered = [rec]
            return v

        # Item 4: a strong title match whose authors are *entirely* absent AND
        # whose year is far off is most likely a DIFFERENT work that happens to
        # share the title — a review, book chapter, erratum, or citing article —
        # not the cited paper miscited. (Contrast: a wrong-author citation of the
        # real paper still has the right *year*, so this guard leaves it alone.)
        author_absent = (
            first_author_check is not None and not first_author_check.ok
            and (authors_check is None or authors_check.similarity < 0.34)
        )
        year_far = (year_check is not None and not year_check.ok
                    and year_check.severity == "major")
        if author_absent and year_far:
            messages.append(
                f"Found a same-titled work by different authors "
                f"(“{rec.title}” — {', '.join(rec.authors[:3]) or 'unknown'}, "
                f"{rec.year}); this looks like a review or citing work, not the "
                f"cited paper. Could not confirm the citation — likely needs a "
                f"DOI or a Google Scholar check."
            )
            v = Verdict(claim, NOT_FOUND, conf, None, checks, messages)
            v.considered = [rec]
            return v

    # --- Field-level agreement on the (now-confirmed) same paper ---
    majors = [c for c in checks if not c.ok and c.severity == "major"]
    minors = [c for c in checks if not c.ok and c.severity == "minor"]

    if majors:
        for c in majors:
            messages.append(f"{c.field}: {c.note or 'mismatch'} "
                            f"(claimed={c.claimed!r}, found={c.found!r}).")
        return Verdict(claim, METADATA_MISMATCH, conf, rec, checks, messages)

    # Item 5: when title and authors match cleanly, a ±1-year gap is just
    # online-first vs. print drift — verify it, don't demote it to a mismatch.
    title_strong = title_check is not None and title_check.similarity >= 0.95
    authors_clean = ((first_author_check is None or first_author_check.ok)
                     and (authors_check is None or authors_check.ok))
    only_year_drift = (minors and all(c.field == "year" for c in minors)
                       and all(c.similarity >= 0.8 for c in minors))
    if only_year_drift and title_strong and authors_clean:
        messages.append(f"Verified against {rec.source} (matched by "
                        f"{rec.matched_by}); note: cited year {claim.year} vs. "
                        f"{rec.year} on record (online-first vs. print).")
        return Verdict(claim, VERIFIED, conf, rec, checks, messages)

    if minors:
        for c in minors:
            messages.append(f"{c.field}: {c.note or 'minor difference'}.")
        return Verdict(claim, MINOR_MISMATCH, conf, rec, checks, messages)

    messages.append(f"Verified against {rec.source} "
                    f"(matched by {rec.matched_by}).")
    return Verdict(claim, VERIFIED, conf, rec, checks, messages)


def record_match_score(claim: Claim, rec: Record) -> float:
    """Score how well a candidate record matches the claim (for ranking).

    Author agreement is weighted heavily so that, among several works sharing a
    title (e.g. a paper and a later review of it), the one actually written by
    the cited authors wins.
    """
    score = title_similarity(claim.title, rec.title) if claim.title else 0.0
    if claim.authors and rec.authors:
        c = set(surnames(claim.authors))
        r = set(surnames(rec.authors))
        if c and r:
            # First-author agreement is the strongest signal of "same work".
            if surname(claim.authors[0]) in r:
                score += 0.5
            score += 0.3 * (len(c & r) / len(c))
    if claim.year and rec.year:
        diff = abs(claim.year - rec.year)
        if diff <= 1:
            score += 0.1
        elif diff >= 3:
            score -= 0.15   # penalize records from a very different year
    return score


def best_record(claim: Claim, candidates: List[Record]) -> Optional[Record]:
    """Pick the record most likely to be the claimed work from title-search hits."""
    best: Optional[Tuple[float, Record]] = None
    for rec in candidates:
        sim = record_match_score(claim, rec)
        if best is None or sim > best[0]:
            best = (sim, rec)
    return best[1] if best else None
