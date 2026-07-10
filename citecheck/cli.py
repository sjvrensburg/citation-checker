"""Command-line interface for citecheck."""

from __future__ import annotations

import argparse
import json
import sys
from typing import List

from citecheck import __version__, report
from citecheck.matching import STRICT, LENIENT
from citecheck.models import Claim, PROBLEM_STATUSES, NEEDS_SCHOLAR
from citecheck.parsers import detect_and_parse, parse_bibtex, extract_cite_keys
from citecheck.verify import VerifyOptions, verify_all
from citecheck.scholar import match_scholar_results


def _read(path: str) -> str:
    if path == "-":
        return sys.stdin.read()
    with open(path, "r", encoding="utf-8", errors="replace") as f:
        return f.read()


def _eprint(msg: str) -> None:
    print(msg, file=sys.stderr)


# ---------------------------------------------------------------------------
# `check`
# ---------------------------------------------------------------------------

def cmd_check(args: argparse.Namespace) -> int:
    if args.string:
        # Treat the positional as a literal citation string, not a path.
        from citecheck.parsers import parse_loose
        claims = parse_loose(args.input)
    else:
        text = _read(args.input)
        claims = detect_and_parse(text, args.format, filename=args.input)

    if not claims:
        _eprint("No citations found in input.")
        return 2

    # Optional LaTeX \cite consistency check against a .bib input.
    tex_notes: List[str] = []
    if args.tex:
        keys = extract_cite_keys(_read(args.tex))
        bib_keys = {c.key for c in claims}
        undefined = [k for k in keys if k not in bib_keys]
        unused = [k for k in bib_keys if k not in keys]
        if undefined:
            tex_notes.append(f"Undefined \\cite keys (in {args.tex}, not in .bib): "
                             + ", ".join(undefined))
        if unused:
            tex_notes.append(f"Unused .bib entries (never \\cite'd): "
                             + ", ".join(sorted(unused)))
        if not undefined and not unused:
            tex_notes.append("LaTeX \\cite keys and .bib entries are consistent.")

    th = LENIENT if args.lenient else STRICT
    progress = (lambda m: _eprint(m)) if args.progress else (lambda m: None)
    opts = VerifyOptions(thresholds=th, use_scholar=not args.no_scholar,
                         progress=progress)

    verdicts = verify_all(claims, opts)

    if args.format_out == "json":
        rendered = report.to_json(verdicts)
    elif args.format_out == "markdown":
        rendered = report.to_markdown(verdicts)
    else:
        rendered = report.to_terminal(verdicts, verbose=args.verbose)

    if tex_notes and args.format_out != "json":
        rendered += "\n\nLaTeX consistency:\n" + "\n".join("  - " + n for n in tex_notes)

    if args.output:
        with open(args.output, "w", encoding="utf-8") as f:
            f.write(rendered)
        _eprint(f"Report written to {args.output}")
    else:
        print(rendered)

    problems = sum(1 for v in verdicts if v.status in PROBLEM_STATUSES)
    pending = sum(1 for v in verdicts if v.status == NEEDS_SCHOLAR)
    if problems:
        return 1
    if pending and args.fail_on_pending:
        return 1
    return 0


# ---------------------------------------------------------------------------
# `scholar-verdict` — close the browser-act feedback loop
# ---------------------------------------------------------------------------

def cmd_scholar_verdict(args: argparse.Namespace) -> int:
    """Re-decide a single claim using rows scraped from Google Scholar.

    Input JSON: {"claim": {...Claim fields...}, "results": [{title, authorline,
    venueYear, citedBy, dataCid, fullTextUrl}, ...]}
    Emits the verdict as JSON.
    """
    payload = json.loads(_read(args.input))
    cd = payload.get("claim", {})
    claim = Claim(
        key=cd.get("key", "scholar-1"), raw=cd.get("raw", ""),
        title=cd.get("title"), authors=cd.get("authors", []) or [],
        year=cd.get("year"), venue=cd.get("venue"),
        doi=cd.get("doi"), arxiv_id=cd.get("arxiv_id"),
    )
    results = payload.get("results", []) or []
    th = LENIENT if args.lenient else STRICT
    verdict = match_scholar_results(claim, results, th)
    print(report.to_json([verdict]))
    return 0 if verdict.status not in PROBLEM_STATUSES else 1


# ---------------------------------------------------------------------------
# Argument parsing
# ---------------------------------------------------------------------------

def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="citecheck",
        description="Verify academic citations against canonical scholarly "
                    "sources (Crossref, OpenAlex, Semantic Scholar, arXiv, "
                    "DataCite), catching valid-DOI-wrong-paper and wrong-"
                    "metadata fabrications. Unresolved items are queued for a "
                    "Google Scholar fallback.")
    p.add_argument("--version", action="version",
                   version=f"citecheck {__version__}")
    sub = p.add_subparsers(dest="command", required=True)

    c = sub.add_parser("check", help="verify citations in a file or string")
    c.add_argument("input", help="path to .bib/.tex/.md/.txt, '-' for stdin, "
                                  "or a citation string with --string")
    c.add_argument("--string", action="store_true",
                   help="treat INPUT as a literal citation string, not a path")
    c.add_argument("-f", "--format", default="auto",
                   choices=["auto", "bibtex", "prose", "loose"],
                   help="input format (default: auto-detect)")
    c.add_argument("-o", "--output", help="write report to FILE instead of stdout")
    c.add_argument("--format-out", default="text",
                   choices=["text", "markdown", "json"],
                   help="report format (default: text)")
    c.add_argument("--tex", help="a .tex file to cross-check \\cite keys against "
                                 "the .bib input (undefined/unused citations)")
    c.add_argument("--strict", action="store_true",
                   help="strict matching thresholds (default)")
    c.add_argument("--lenient", action="store_true",
                   help="lenient matching (drafts): looser title/author/year tolerance")
    c.add_argument("--no-scholar", action="store_true",
                   help="do not queue unresolved citations for Google Scholar; "
                        "mark them NOT_FOUND (fully headless/CI mode)")
    c.add_argument("--fail-on-pending", action="store_true",
                   help="exit non-zero if any citation needs a Scholar check")
    c.add_argument("-v", "--verbose", action="store_true",
                   help="show per-field comparison details")
    c.add_argument("--progress", action="store_true",
                   help="print progress to stderr")
    c.set_defaults(func=cmd_check)

    s = sub.add_parser("scholar-verdict",
                       help="decide a claim from scraped Google Scholar rows "
                            "(closes the browser-act fallback loop)")
    s.add_argument("input", help="JSON file with {claim, results}, '-' for stdin")
    s.add_argument("--lenient", action="store_true")
    s.set_defaults(func=cmd_scholar_verdict)

    return p


def main(argv: List[str] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return args.func(args)
    except FileNotFoundError as e:
        _eprint(f"Error: file not found: {e.filename}")
        return 2
    except KeyboardInterrupt:
        _eprint("Interrupted.")
        return 130


if __name__ == "__main__":
    sys.exit(main())
