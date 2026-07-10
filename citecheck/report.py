"""Render verdicts as terminal text, Markdown, or JSON."""

from __future__ import annotations

import json
from typing import Dict, List

from citecheck.models import (
    Verdict, VERIFIED, MINOR_MISMATCH, METADATA_MISMATCH, DOI_MISMATCH,
    NOT_FOUND, NEEDS_SCHOLAR, ERROR,
)

_ORDER = [DOI_MISMATCH, METADATA_MISMATCH, NOT_FOUND, NEEDS_SCHOLAR,
          MINOR_MISMATCH, ERROR, VERIFIED]

_ICON = {
    VERIFIED: "✅", MINOR_MISMATCH: "🟡", METADATA_MISMATCH: "❌",
    DOI_MISMATCH: "🚨", NOT_FOUND: "⛔", NEEDS_SCHOLAR: "🔎", ERROR: "⚠️",
}

_LABEL = {
    VERIFIED: "Verified",
    MINOR_MISMATCH: "Minor mismatch",
    METADATA_MISMATCH: "Metadata mismatch",
    DOI_MISMATCH: "Identifier points to a DIFFERENT paper",
    NOT_FOUND: "Not found (likely fabricated)",
    NEEDS_SCHOLAR: "Needs Google Scholar check",
    ERROR: "Inconclusive (lookup error)",
}


def counts(verdicts: List[Verdict]) -> Dict[str, int]:
    c: Dict[str, int] = {}
    for v in verdicts:
        c[v.status] = c.get(v.status, 0) + 1
    return c


def _sort_key(v: Verdict) -> int:
    try:
        return _ORDER.index(v.status)
    except ValueError:
        return len(_ORDER)


def to_terminal(verdicts: List[Verdict], verbose: bool = False) -> str:
    lines: List[str] = []
    c = counts(verdicts)
    total = len(verdicts)
    lines.append("=" * 64)
    lines.append(f"Citation check — {total} citation(s)")
    lines.append("=" * 64)
    for status in _ORDER:
        if c.get(status):
            lines.append(f"  {_ICON[status]} {_LABEL[status]}: {c[status]}")
    lines.append("")

    for v in sorted(verdicts, key=_sort_key):
        icon = _ICON.get(v.status, "•")
        lines.append(f"{icon} [{v.claim.key}] {_LABEL.get(v.status, v.status)}")
        lines.append(f"    cite: {v.claim.describe()[:100]}")
        for m in v.messages:
            lines.append(f"    → {m}")
        if v.record and (verbose or v.status in (DOI_MISMATCH, METADATA_MISMATCH)):
            r = v.record
            lines.append(f"    found: “{r.title}” — "
                         f"{', '.join(r.authors[:3])}{' et al.' if len(r.authors) > 3 else ''}"
                         f" ({r.year}) {r.venue or ''} [{r.source}]")
        if verbose:
            for fc in v.field_checks:
                mark = "ok" if fc.ok else fc.severity.upper()
                lines.append(f"      · {fc.field}: {mark} sim={fc.similarity:.2f}"
                             f" claimed={fc.claimed!r} found={fc.found!r}")
        if v.scholar_query:
            lines.append(f"    scholar query: {v.scholar_query}")
        lines.append("")
    return "\n".join(lines)


def to_markdown(verdicts: List[Verdict]) -> str:
    c = counts(verdicts)
    total = len(verdicts)
    out = ["# Citation Verification Report", ""]
    out.append(f"**{total}** citation(s) checked.")
    out.append("")
    out.append("| Status | Count |")
    out.append("|---|---|")
    for status in _ORDER:
        if c.get(status):
            out.append(f"| {_ICON[status]} {_LABEL[status]} | {c[status]} |")
    out.append("")

    for status in _ORDER:
        group = [v for v in verdicts if v.status == status]
        if not group:
            continue
        out.append(f"## {_ICON[status]} {_LABEL[status]} ({len(group)})")
        out.append("")
        for v in group:
            out.append(f"### `{v.claim.key}`")
            out.append("")
            out.append(f"- **Citation:** {v.claim.describe()}")
            for m in v.messages:
                out.append(f"- {m}")
            if v.record:
                r = v.record
                out.append(f"- **Found:** “{r.title}” — "
                           f"{', '.join(r.authors[:5])} ({r.year}) "
                           f"{r.venue or ''} · {r.source}"
                           + (f" · doi:{r.doi}" if r.doi else ""))
            if v.field_checks:
                bad = [fc for fc in v.field_checks if not fc.ok]
                for fc in bad:
                    out.append(f"    - `{fc.field}` {fc.severity}: "
                               f"claimed `{fc.claimed}` vs found `{fc.found}` "
                               f"(sim {fc.similarity:.2f})")
            if v.scholar_query:
                out.append(f"- **Google Scholar query:** `{v.scholar_query}`")
            out.append("")
    return "\n".join(out)


def to_json(verdicts: List[Verdict]) -> str:
    return json.dumps({
        "summary": counts(verdicts),
        "total": len(verdicts),
        "results": [v.to_dict() for v in verdicts],
    }, indent=2, ensure_ascii=False)
