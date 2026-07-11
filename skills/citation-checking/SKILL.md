---
name: citation-checking
description: >-
  Rigorously verify academic citations against canonical scholarly sources
  instead of merely checking that a DOI resolves. Catches the failure modes of
  AI-generated references: a DOI that is valid but points to a DIFFERENT paper,
  a real paper cited with the wrong authors/year/journal, and wholly fabricated
  references. Use whenever the user asks to "check citations", "verify
  references", "check sources", validate a .bib / bibliography / reference list,
  or asks whether cited papers are real. Uses Crossref, OpenAlex, Semantic
  Scholar, arXiv, and DataCite first, then falls back to Google Scholar (driven
  through browser-act) only for items the APIs cannot resolve.
---

# Citation Checking

## Why this exists

Checking that a DOI *resolves* is not verification. A fabricated citation can
carry a DOI that is perfectly valid but belongs to an unrelated paper; or the
paper can be real while the authors, year, or venue are wrong; or the reference
can be invented outright. This skill verifies the **identity and every asserted
field** of each citation against authoritative metadata, and flags exactly what
disagrees.

The heavy lifting is done by a zero-dependency Python CLI, `citecheck`, in this
repository. It needs only `python3` (3.8+) — no `pip install`, so it runs
identically under Claude Code, Codex, or CI.

## When to use

- The user asks to check/verify citations, references, sources, or a `.bib`.
- Before submitting any document whose bibliography was produced or touched by
  an LLM.
- After you yourself generate citations in a draft (verify before presenting).

## The one rule

**Never assert that a citation is correct from memory or from a resolving DOI
alone. Always run `citecheck`.** If `citecheck` cannot confirm a citation and
Google Scholar is unavailable, report it as *unverified* — never upgrade a guess
to a fact.

## Quick start

Find the tool (this repo). From its root:

```bash
# A BibTeX file (auto-detected)
python3 -m citecheck check references.bib

# A prose/Markdown document with a References section
python3 -m citecheck check paper.md

# A LaTeX file with an embedded \bibitem/thebibliography block (keys preserved)
python3 -m citecheck check paper.tex

# A loose list of DOIs / arXiv IDs / one-line citations (or stdin)
python3 -m citecheck check sources.txt
echo "Vaswani et al. 2017, Attention Is All You Need, NeurIPS" | \
  python3 -m citecheck check - --format loose

# A single citation string
python3 -m citecheck check "Smith, J. (2020). Some title. Nature." --string

# Cross-check \cite keys in a LaTeX file against the .bib
python3 -m citecheck check refs.bib --tex paper.tex
```

Useful flags: `--format-out {text,markdown,json}`, `-o report.md`, `-v`
(per-field detail), `--lenient` (draft-stage tolerances), `--no-scholar` (pure
headless/CI — unresolved items become `NOT_FOUND` instead of being queued for
Scholar), `--progress`.

Set `CITECHECK_MAILTO=you@example.com` in the environment so the Crossref
"polite pool" is used (faster, more reliable).

## Reading the verdicts

Each citation gets one status (see `references/verdicts.md` for the full table):

| Status | Meaning | What to do |
|---|---|---|
| ✅ `VERIFIED` | Resolved; all checked fields agree | Nothing |
| 🟡 `MINOR_MISMATCH` | Same paper; small diffs (venue abbrev, preprint-year lag, a claimed co-author absent from the record) | Read the notes — an absent co-author means the author list needs fixing |
| ❌ `METADATA_MISMATCH` | Right paper, but **wrong author/year/venue** as cited | Fix the citation to match the found record |
| 🚨 `DOI_MISMATCH` | The DOI/arXiv id resolves to a **different paper** | Replace the identifier or the citation — this is the classic fabrication |
| ⛔ `NOT_FOUND` | No canonical source matches | Treat as fabricated unless Scholar confirms |
| 🔎 `NEEDS_SCHOLAR` | APIs couldn't resolve it; queued for Google Scholar | Run the Scholar fallback (below) |
| ⚠️ `ERROR` | Network/parse failure | Re-run; inconclusive, do not pass |

`citecheck check` exits `1` if any `DOI_MISMATCH`, `METADATA_MISMATCH`, or
`NOT_FOUND` is present — handy in CI. Add `--fail-on-pending` to also fail on
`NEEDS_SCHOLAR`.

**Always report mismatches with the specific diff** (what was claimed vs. what
the source says), not just a pass/fail count. Use `-v` or `--format-out markdown`
to surface the field-level detail to the user. Read `MINOR_MISMATCH` notes too —
a "claimed co-author(s) not found in record" note is a fabrication marker (AI
author lists often graft one fake name onto a real paper), and preprint-lag /
stub-title notes explain year and title gaps that are *not* errors.

Two things the tool cannot see, so check them yourself on critical references:
given-name errors that keep the surname ("Manuel" vs. "Matthias" Kirchler —
surname matching passes), and `NEEDS_SCHOLAR` items that are famous-but-unindexed
classics (Black 1976, RiskMetrics 1996) vs. genuinely suspicious working papers —
the Scholar fallback decides those.

## Google Scholar fallback (only for NEEDS_SCHOLAR)

The canonical APIs cover the large majority of the literature and never CAPTCHA.
Google Scholar has no API and blocks bots aggressively, so use it **only** for
the handful of citations `citecheck` marks `NEEDS_SCHOLAR` (often books, theses,
or non-English grey literature). Drive it through the **browser-act** skill —
see `references/scholar-fallback.md` for the exact procedure. In short:

1. Get the `NEEDS_SCHOLAR` items and their `scholar_query` (use
   `--format-out json`).
2. For each, use browser-act to open the Scholar results and scrape the rows.
   **Launch headed** (`browser-act browser open … --headed`) — browser-act
   defaults to headless, which almost always triggers Scholar's CAPTCHA;
   a headed browser on an established profile usually sails through, and the
   user can clear any challenge that does appear.
3. Feed the scraped rows back through `python3 -m citecheck scholar-verdict` to
   get a proper field-by-field verdict (do **not** eyeball it).
4. If Scholar shows a CAPTCHA, **stop and ask the user to solve it** — never
   hammer it.

## Workflow to follow

1. Identify the input (a `.bib`, a document with a reference list, or a list of
   identifiers). Point `citecheck check` at it.
2. Read the report. For `DOI_MISMATCH` / `METADATA_MISMATCH`, show the user the
   exact discrepancy and the correct metadata `citecheck` found.
3. For `NEEDS_SCHOLAR`, run the Scholar fallback via browser-act, then re-decide
   with `scholar-verdict`.
4. Summarize: how many verified, how many need fixing, and the concrete fix for
   each problem citation. Never silently "pass" a citation you could not verify.

## References

- `references/verdicts.md` — full status semantics and thresholds.
- `references/scholar-fallback.md` — step-by-step Google Scholar + browser-act
  procedure and the DOM-scraping snippet.
