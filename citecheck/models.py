"""Core data structures shared across parsing, source lookup, and matching."""

from __future__ import annotations

from dataclasses import dataclass, field, asdict
from typing import Any, Dict, List, Optional


@dataclass
class Claim:
    """What a citation *asserts* — the values a paper claims to be true.

    Any field may be ``None``/empty when the source citation omits it.
    """

    key: str = ""                       # citation key or label (e.g. bibtex ID, "[12]")
    raw: str = ""                       # original text of the citation, for reporting
    title: Optional[str] = None
    authors: List[str] = field(default_factory=list)
    year: Optional[int] = None
    venue: Optional[str] = None         # journal / conference / publisher
    doi: Optional[str] = None
    arxiv_id: Optional[str] = None
    url: Optional[str] = None
    entry_type: Optional[str] = None    # bibtex type: article, inproceedings, ...

    def has_identifier(self) -> bool:
        return bool(self.doi or self.arxiv_id)

    def describe(self) -> str:
        bits = []
        if self.authors:
            bits.append(self.authors[0])
        if self.year:
            bits.append(f"({self.year})")
        if self.title:
            bits.append(f"“{self.title}”")
        return " ".join(bits) or self.raw or self.key or "<empty citation>"


@dataclass
class Record:
    """What a canonical source *returned* for a lookup."""

    source: str                          # crossref | openalex | semanticscholar | arxiv | datacite | scholar
    matched_by: str                      # doi | arxiv | title-search | manual
    title: Optional[str] = None
    authors: List[str] = field(default_factory=list)
    year: Optional[int] = None
    venue: Optional[str] = None
    doi: Optional[str] = None
    arxiv_id: Optional[str] = None
    url: Optional[str] = None
    citation_count: Optional[int] = None
    extra: Dict[str, Any] = field(default_factory=dict)


@dataclass
class FieldCheck:
    """Outcome of comparing one claimed field against the resolved record."""

    field: str                           # title | first_author | authors | year | venue | doi
    claimed: Any
    found: Any
    similarity: float                    # 0.0 - 1.0
    ok: bool
    severity: str = "info"               # info | minor | major | critical
    note: str = ""


# Verdict statuses, ordered from most to least trustworthy.
VERIFIED = "VERIFIED"                     # resolved and every checked field agrees
MINOR_MISMATCH = "MINOR_MISMATCH"        # resolved; only cosmetic differences (venue abbrev, year off by 1)
METADATA_MISMATCH = "METADATA_MISMATCH"  # right paper, but wrong author/year/venue as claimed
DOI_MISMATCH = "DOI_MISMATCH"            # identifier resolves to a DIFFERENT paper than claimed
NOT_FOUND = "NOT_FOUND"                  # nothing canonical matches -> likely fabricated
NEEDS_SCHOLAR = "NEEDS_SCHOLAR"          # unresolved by APIs; queued for Google Scholar fallback
ERROR = "ERROR"                          # network/parse failure; inconclusive

# Statuses that represent a real integrity problem (non-zero exit code).
PROBLEM_STATUSES = {METADATA_MISMATCH, DOI_MISMATCH, NOT_FOUND}


@dataclass
class Verdict:
    claim: Claim
    status: str
    confidence: float = 0.0              # 0.0 - 1.0
    record: Optional[Record] = None
    field_checks: List[FieldCheck] = field(default_factory=list)
    messages: List[str] = field(default_factory=list)
    scholar_query: Optional[str] = None  # populated for NEEDS_SCHOLAR
    considered: List[Record] = field(default_factory=list)  # candidate records seen

    def to_dict(self) -> Dict[str, Any]:
        d = {
            "key": self.claim.key,
            "citation": self.claim.describe(),
            "status": self.status,
            "confidence": round(self.confidence, 3),
            "messages": self.messages,
            "claim": asdict(self.claim),
            "record": asdict(self.record) if self.record else None,
            "field_checks": [asdict(fc) for fc in self.field_checks],
        }
        if self.scholar_query:
            d["scholar_query"] = self.scholar_query
        return d
