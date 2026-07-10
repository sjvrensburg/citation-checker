"""The verification pipeline: resolve each claim against canonical sources,
then decide a verdict. Google Scholar is deliberately NOT called here — the API
tier is fast, reliable, and CAPTCHA-free. Claims the APIs cannot resolve are
marked NEEDS_SCHOLAR and handed to the browser-driven fallback (see scholar.py).
"""

from __future__ import annotations

from typing import Callable, List, Optional

from citecheck import sources as S
from citecheck.matching import Thresholds, STRICT, decide, best_record, title_similarity
from citecheck.models import (
    Claim, Record, Verdict, NOT_FOUND, NEEDS_SCHOLAR, ERROR,
)
from citecheck.scholar import scholar_query_for


class VerifyOptions:
    def __init__(self, thresholds: Thresholds = STRICT, use_scholar: bool = True,
                 progress: Optional[Callable[[str], None]] = None):
        self.thresholds = thresholds
        self.use_scholar = use_scholar          # if False, unresolved -> NOT_FOUND
        self.progress = progress or (lambda _m: None)


# ---------------------------------------------------------------------------
# Identity resolution
# ---------------------------------------------------------------------------

def _resolve_by_identifier(claim: Claim) -> Optional[Record]:
    """Authoritative lookup when the citation carries a DOI or arXiv id."""
    if claim.doi:
        for fn in (S.crossref_by_doi, S.datacite_by_doi, S.openalex_by_doi, S.s2_by_doi):
            try:
                rec = fn(claim.doi)
            except Exception:
                rec = None
            if rec:
                return rec
    if claim.arxiv_id:
        try:
            rec = S.arxiv_by_id(claim.arxiv_id)
        except Exception:
            rec = None
        if rec:
            return rec
    return None


def _resolve_by_title(claim: Claim) -> List[Record]:
    """Best-effort discovery when there is no usable identifier."""
    if not claim.title:
        return []
    author = None
    if claim.authors:
        from citecheck.matching import surname
        author = surname(claim.authors[0])
    candidates: List[Record] = []
    searchers = [
        lambda: S.crossref_search(claim.title, author),
        lambda: S.openalex_search(claim.title),
        lambda: S.s2_search(claim.title),
    ]
    for fn in searchers:
        try:
            candidates.extend(fn())
        except Exception:
            continue
        # Early exit: a near-perfect title hit is enough.
        for c in candidates:
            if title_similarity(claim.title, c.title) >= 0.95:
                return candidates
    return candidates


def verify_claim(claim: Claim, opts: VerifyOptions) -> Verdict:
    th = opts.thresholds
    opts.progress(f"  checking: {claim.describe()[:80]}")

    # An empty claim (no title, no id) is unverifiable by construction.
    if not claim.title and not claim.has_identifier():
        v = Verdict(claim, NOT_FOUND, 0.0, None, [],
                    ["Citation has neither a title nor an identifier to check."])
        return v

    # 1) Identifier path (authoritative; also catches "valid DOI, wrong paper").
    rec = _resolve_by_identifier(claim)
    if rec:
        # If the claim has a title, decide() cross-checks DOI->paper identity.
        # If it has no title, we still verify the identifier exists.
        return decide(claim, rec, th)

    # An identifier was supplied but resolved nowhere: strong fabrication signal.
    if claim.has_identifier():
        ident = claim.doi or claim.arxiv_id
        msg = [f"Identifier '{ident}' does not resolve on Crossref/DataCite/"
               f"OpenAlex/arXiv/Semantic Scholar — it appears to be invalid or fabricated."]
        # Still try a title search to see if the *paper* exists under another id.
        cands = _resolve_by_title(claim)
        best = best_record(claim, cands) if cands else None
        if best and title_similarity(claim.title, best.title) >= th.title_same:
            v = decide(claim, best, th)
            v.messages.insert(0, msg[0])
            v.messages.insert(1, f"However, a matching paper exists with "
                                 f"{best.source} id/doi={best.doi or best.url}.")
            return v
        v = Verdict(claim, NOT_FOUND, 0.0, None, [], msg)
        if opts.use_scholar:
            v.status = NEEDS_SCHOLAR
            v.scholar_query = scholar_query_for(claim)
            v.messages.append("Queued for Google Scholar fallback check.")
        return v

    # 2) Title-search path.
    try:
        cands = _resolve_by_title(claim)
    except Exception as e:
        return Verdict(claim, ERROR, 0.0, None, [],
                       [f"Lookup failed: {e}"])
    best = best_record(claim, cands) if cands else None
    if best is not None:
        v = decide(claim, best, th)
        v.considered = cands[:5]
        # decide() may downgrade a weak title match to NOT_FOUND already.
        if v.status == NOT_FOUND and opts.use_scholar:
            v.status = NEEDS_SCHOLAR
            v.scholar_query = scholar_query_for(claim)
            v.messages.append("Queued for Google Scholar fallback check.")
        return v

    # 3) Nothing found anywhere.
    msg = ["Not found in Crossref, OpenAlex, Semantic Scholar, or arXiv."]
    v = Verdict(claim, NOT_FOUND, 0.0, None, [], msg)
    if opts.use_scholar:
        v.status = NEEDS_SCHOLAR
        v.scholar_query = scholar_query_for(claim)
        v.messages.append("Queued for Google Scholar fallback check "
                          "(may exist as a book, thesis, or grey literature).")
    return v


def verify_all(claims: List[Claim], opts: VerifyOptions) -> List[Verdict]:
    verdicts = []
    for i, claim in enumerate(claims, 1):
        opts.progress(f"[{i}/{len(claims)}] {claim.key}")
        verdicts.append(verify_claim(claim, opts))
    return verdicts
