"""Canonical scholarly source clients.

Each function returns a normalized :class:`Record` (or ``None`` if not found).
All sources are free and keyless. Preference order for identity resolution:

    DOI      -> Crossref, then DataCite, then OpenAlex   (publisher-authoritative)
    arXiv ID -> arXiv Atom API
    title    -> Crossref bibliographic, OpenAlex, Semantic Scholar (best-effort)

Network/parse failures raise; callers decide whether to treat as ERROR.
"""

from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from typing import List, Optional

from citecheck.http import get_json, get_text, HttpError
from citecheck.models import Record


# ---------------------------------------------------------------------------
# Identifier normalization
# ---------------------------------------------------------------------------

def clean_doi(doi: Optional[str]) -> Optional[str]:
    if not doi:
        return None
    doi = doi.strip()
    doi = re.sub(r"^https?://(dx\.)?doi\.org/", "", doi, flags=re.I)
    doi = re.sub(r"^doi:\s*", "", doi, flags=re.I)
    return doi or None


def clean_arxiv(arxiv_id: Optional[str]) -> Optional[str]:
    if not arxiv_id:
        return None
    a = arxiv_id.strip()
    a = re.sub(r"^https?://arxiv\.org/(abs|pdf)/", "", a, flags=re.I)
    a = re.sub(r"^arxiv:\s*", "", a, flags=re.I)
    a = re.sub(r"v\d+$", "", a)          # drop version suffix
    a = a.replace(".pdf", "")
    return a or None


def _year_from_parts(parts) -> Optional[int]:
    try:
        return int(parts[0][0])
    except (IndexError, TypeError, ValueError):
        return None


# ---------------------------------------------------------------------------
# Crossref
# ---------------------------------------------------------------------------

def _crossref_record(msg: dict, matched_by: str) -> Record:
    title = (msg.get("title") or [""])[0] or None
    # Crossref often registers "Title: Subtitle" works with the subtitle in a
    # separate field (e.g. ACM's "Optuna" + "A Next-generation Hyperparameter
    # Optimization Framework"). Recombine, or title similarity collapses.
    subtitle = (msg.get("subtitle") or [""])[0]
    if title and subtitle and subtitle.lower() not in title.lower():
        title = f"{title}: {subtitle}"
    authors = []
    for a in msg.get("author", []) or []:
        given, family = a.get("given", ""), a.get("family", "")
        name = (f"{given} {family}").strip() or a.get("name", "")
        if name:
            authors.append(name)
    year = None
    for key in ("published-print", "published-online", "published", "issued", "created"):
        if key in msg and msg[key].get("date-parts"):
            year = _year_from_parts(msg[key]["date-parts"])
            if year:
                break
    venue = (msg.get("container-title") or [None])
    venue = venue[0] if venue else None
    return Record(
        source="crossref", matched_by=matched_by, title=title, authors=authors,
        year=year, venue=venue, doi=(msg.get("DOI") or None),
        url=msg.get("URL"), citation_count=msg.get("is-referenced-by-count"),
        extra={"type": msg.get("type")},
    )


def crossref_by_doi(doi: str) -> Optional[Record]:
    doi = clean_doi(doi)
    if not doi:
        return None
    try:
        data = get_json(f"https://api.crossref.org/works/{doi}")
    except HttpError as e:
        if e.status == 404:
            return None
        raise
    msg = data.get("message")
    return _crossref_record(msg, "doi") if msg else None


def crossref_search(claim_title: str, author: Optional[str] = None,
                    rows: int = 5) -> List[Record]:
    params = {"query.bibliographic": claim_title, "rows": rows,
              "select": "title,subtitle,author,issued,container-title,DOI,URL,type,is-referenced-by-count"}
    if author:
        params["query.author"] = author
    try:
        data = get_json("https://api.crossref.org/works", params=params)
    except HttpError as e:
        if e.status == 404:
            return []
        raise
    items = data.get("message", {}).get("items", []) or []
    return [_crossref_record(it, "title-search") for it in items]


def crossref_bibtex(doi: str) -> Optional[str]:
    doi = clean_doi(doi)
    if not doi:
        return None
    try:
        return get_text(f"https://doi.org/{doi}", accept="application/x-bibtex")
    except HttpError:
        return None


# ---------------------------------------------------------------------------
# DataCite (DOIs not registered with Crossref: datasets, some books, Zenodo)
# ---------------------------------------------------------------------------

def datacite_by_doi(doi: str) -> Optional[Record]:
    doi = clean_doi(doi)
    if not doi:
        return None
    try:
        data = get_json(f"https://api.datacite.org/dois/{doi}")
    except HttpError as e:
        if e.status == 404:
            return None
        raise
    attr = data.get("data", {}).get("attributes", {})
    if not attr:
        return None
    titles = attr.get("titles") or []
    title = titles[0].get("title") if titles else None
    authors = []
    for c in attr.get("creators", []) or []:
        name = c.get("name") or " ".join(
            filter(None, [c.get("givenName"), c.get("familyName")]))
        if name:
            authors.append(name)
    year = attr.get("publicationYear")
    try:
        year = int(year) if year else None
    except (TypeError, ValueError):
        year = None
    return Record(source="datacite", matched_by="doi", title=title,
                  authors=authors, year=year, venue=attr.get("publisher"),
                  doi=attr.get("doi"), url=attr.get("url"))


# ---------------------------------------------------------------------------
# arXiv (Atom XML)
# ---------------------------------------------------------------------------

_ATOM = "{http://www.w3.org/2005/Atom}"


def _arxiv_entry_to_record(entry, matched_by: str) -> Record:
    title = (entry.findtext(f"{_ATOM}title") or "").strip() or None
    authors = [ (a.findtext(f"{_ATOM}name") or "").strip()
                for a in entry.findall(f"{_ATOM}author") ]
    authors = [a for a in authors if a]
    published = entry.findtext(f"{_ATOM}published") or ""
    year = None
    m = re.match(r"(\d{4})", published)
    if m:
        year = int(m.group(1))
    id_url = entry.findtext(f"{_ATOM}id") or ""
    arxiv_id = clean_arxiv(id_url)
    doi = entry.findtext("{http://arxiv.org/schemas/atom}doi")
    return Record(source="arxiv", matched_by=matched_by, title=title,
                  authors=authors, year=year, venue="arXiv",
                  arxiv_id=arxiv_id, doi=doi, url=id_url or None)


def arxiv_by_id(arxiv_id: str) -> Optional[Record]:
    arxiv_id = clean_arxiv(arxiv_id)
    if not arxiv_id:
        return None
    xml = get_text("http://export.arxiv.org/api/query",
                   accept="application/atom+xml",
                   params={"id_list": arxiv_id, "max_results": 1})
    root = ET.fromstring(xml)
    entry = root.find(f"{_ATOM}entry")
    if entry is None:
        return None
    # arXiv returns a placeholder entry with an error title when the id is bad.
    title = entry.findtext(f"{_ATOM}title") or ""
    if title.strip().lower().startswith("error"):
        return None
    return _arxiv_entry_to_record(entry, "arxiv")


def arxiv_search(title: str, rows: int = 5) -> List[Record]:
    query = 'ti:"%s"' % title.replace('"', "")
    xml = get_text("http://export.arxiv.org/api/query",
                   accept="application/atom+xml",
                   params={"search_query": query, "max_results": rows})
    root = ET.fromstring(xml)
    return [_arxiv_entry_to_record(e, "title-search")
            for e in root.findall(f"{_ATOM}entry")]


# ---------------------------------------------------------------------------
# OpenAlex
# ---------------------------------------------------------------------------

def _openalex_record(w: dict, matched_by: str) -> Record:
    authors = []
    for a in w.get("authorships", []) or []:
        name = (a.get("author") or {}).get("display_name")
        if name:
            authors.append(name)
    venue = None
    primary = w.get("primary_location") or {}
    src = primary.get("source") or {}
    venue = src.get("display_name")
    doi = clean_doi(w.get("doi"))
    return Record(source="openalex", matched_by=matched_by,
                  title=w.get("title") or w.get("display_name"),
                  authors=authors, year=w.get("publication_year"),
                  venue=venue, doi=doi, url=w.get("id"),
                  citation_count=w.get("cited_by_count"))


def openalex_by_doi(doi: str) -> Optional[Record]:
    doi = clean_doi(doi)
    if not doi:
        return None
    try:
        w = get_json(f"https://api.openalex.org/works/https://doi.org/{doi}")
    except HttpError as e:
        if e.status == 404:
            return None
        raise
    return _openalex_record(w, "doi") if w and w.get("id") else None


def openalex_search(title: str, rows: int = 5) -> List[Record]:
    try:
        data = get_json("https://api.openalex.org/works",
                        params={"search": title, "per-page": rows})
    except HttpError as e:
        if e.status == 404:
            return []
        raise
    return [_openalex_record(w, "title-search")
            for w in data.get("results", []) or []]


# ---------------------------------------------------------------------------
# Semantic Scholar (Graph API, keyless tier)
# ---------------------------------------------------------------------------

_S2_FIELDS = "title,authors,year,venue,externalIds,citationCount,url"


def _s2_record(p: dict, matched_by: str) -> Record:
    authors = [a.get("name") for a in (p.get("authors") or []) if a.get("name")]
    ext = p.get("externalIds") or {}
    return Record(source="semanticscholar", matched_by=matched_by,
                  title=p.get("title"), authors=authors, year=p.get("year"),
                  venue=p.get("venue") or None,
                  doi=clean_doi(ext.get("DOI")), arxiv_id=ext.get("ArXiv"),
                  url=p.get("url"), citation_count=p.get("citationCount"))


def s2_by_doi(doi: str) -> Optional[Record]:
    doi = clean_doi(doi)
    if not doi:
        return None
    try:
        p = get_json(f"https://api.semanticscholar.org/graph/v1/paper/DOI:{doi}",
                     params={"fields": _S2_FIELDS})
    except HttpError as e:
        if e.status == 404:
            return None
        raise
    return _s2_record(p, "doi") if p and p.get("title") else None


def s2_search(title: str, rows: int = 5) -> List[Record]:
    try:
        data = get_json("https://api.semanticscholar.org/graph/v1/paper/search",
                        params={"query": title, "limit": rows, "fields": _S2_FIELDS})
    except HttpError as e:
        if e.status in (404, 400):
            return []
        raise
    return [_s2_record(p, "title-search") for p in data.get("data", []) or []]
