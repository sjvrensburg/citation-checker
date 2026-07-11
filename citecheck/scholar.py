"""Google Scholar fallback support.

Google Scholar has no API and CAPTCHAs automated access, so citecheck does NOT
scrape it directly. Instead it produces the exact search URL and a matching
routine that an *agent* (Claude Code / Codex) drives through the browser-act
skill. The agent scrapes the result and feeds the metadata back via
``match_scholar_result`` (or the ``citecheck scholar-verdict`` CLI subcommand).

This keeps the Python tool dependency-free and CI-safe while still giving the
agent a precise, mechanical way to use Google Scholar as a last resort.
"""

from __future__ import annotations

import urllib.parse
from typing import List, Optional

from citecheck.matching import Thresholds, STRICT, decide, best_record
from citecheck.models import Claim, Record, Verdict


def scholar_query_for(claim: Claim) -> str:
    """The natural-language query to type into Google Scholar."""
    bits = []
    if claim.title:
        bits.append(claim.title)
    if claim.authors:
        from citecheck.matching import surname
        bits.append(surname(claim.authors[0]))
    if claim.year:
        bits.append(str(claim.year))
    return " ".join(bits).strip() or (claim.doi or claim.arxiv_id or claim.raw)


def scholar_url_for(claim: Claim) -> str:
    q = scholar_query_for(claim)
    return ("https://scholar.google.com/scholar?hl=en&num=10&q="
            + urllib.parse.quote(q))


# The DOM-scraping snippet the agent runs via browser-act's evaluate/JS step.
# Mirrors cookjohn/gs-skills selectors, with CAPTCHA detection.
SCHOLAR_SCRAPE_JS = r"""
(() => {
  if (document.querySelector('#gs_captcha_ccl') ||
      document.body.innerText.includes('unusual traffic')) {
    return JSON.stringify({error: 'captcha'});
  }
  const items = document.querySelectorAll('#gs_res_ccl .gs_r.gs_or.gs_scl');
  const results = Array.from(items).slice(0, 10).map(it => {
    const meta = (it.querySelector('.gs_a')?.textContent || '');
    // Scholar separates author/venue/domain with " - ", but the whitespace
    // around the first hyphen is a non-breaking space — split accordingly.
    const parts = meta.split(/[\s ]+-[\s ]+/);
    return {
      title: (it.querySelector('.gs_rt a') || it.querySelector('.gs_rt'))
               ?.textContent?.trim() || '',
      authorline: parts[0]?.trim() || '',
      venueYear: parts[1]?.trim() || '',
      citedBy: it.querySelector('.gs_fl a[href*="cites"]')
                 ?.textContent?.match(/\d+/)?.[0] || '0',
      dataCid: it.getAttribute('data-cid') || '',
      fullTextUrl: (it.querySelector('.gs_ggs a') ||
                    it.querySelector('.gs_or_ggsm a'))?.href || ''
    };
  });
  return JSON.stringify({results});
})()
"""


def _scholar_result_to_record(item: dict) -> Record:
    """Convert one scraped Google Scholar row into a Record."""
    import re
    title = item.get("title") or ""
    # Citation-only rows carry bracket tags in the title ("[CITATION][C] …",
    # "[BOOK][B] …"), and some entries append editorial annotations
    # ("… [with discussion and reply]"). Strip both or title similarity sinks.
    title = re.sub(r"^(\s*\[[^\]]{1,12}\])+\s*", "", title)
    title = re.sub(r"\s*\[[^\]]*\]\s*$", "", title)
    venue_year = item.get("venueYear", "") or ""
    year = None
    m = re.search(r"\b(1[89]\d{2}|20\d{2})\b", venue_year)
    if m:
        year = int(m.group(1))
    venue = re.sub(r",?\s*\b(1[89]\d{2}|20\d{2})\b.*$", "", venue_year).strip(", ")
    # Google Scholar's author line abbreviates given names ("A Vaswani, N Shazeer").
    authors = [a.strip() for a in (item.get("authorline") or "").split(",") if a.strip()]
    cb = item.get("citedBy")
    try:
        cb = int(cb)
    except (TypeError, ValueError):
        cb = None
    return Record(source="scholar", matched_by="title-search",
                  title=title or None, authors=authors,
                  year=year, venue=venue or None,
                  url=item.get("fullTextUrl") or None, citation_count=cb,
                  extra={"data_cid": item.get("dataCid")})


def match_scholar_results(claim: Claim, scraped_items: List[dict],
                          th: Thresholds = STRICT) -> Verdict:
    """Given rows scraped from Google Scholar, produce a verdict for the claim.

    Note: Google Scholar author lines are heavily abbreviated, so author checks
    are informative but weaker here; title + year carry the decision.
    """
    records = [_scholar_result_to_record(it) for it in scraped_items if it.get("title")]
    if not records:
        from citecheck.models import NOT_FOUND
        return Verdict(claim, NOT_FOUND, 0.0, None, [],
                       ["Google Scholar returned no matching results — "
                        "citation appears fabricated."])
    best = best_record(claim, records)
    v = decide(claim, best, th) if best else None
    if v is None:
        from citecheck.models import NOT_FOUND
        return Verdict(claim, NOT_FOUND, 0.0, None, [],
                       ["No confident Google Scholar match."])
    v.considered = records[:5]
    v.messages.insert(0, "Resolved via Google Scholar fallback (browser). "
                         "Author agreement is approximate (GS abbreviates names).")
    return v
