# citation-checker (`citecheck`)

Rigorously verify academic citations against **canonical scholarly sources** —
not just "does the DOI resolve". Built to catch the failure modes of
AI-generated references:

- 🚨 **Valid DOI, wrong paper** — the identifier resolves, but to a different
  work than the one cited.
- ❌ **Right paper, wrong metadata** — the paper is real, but the authors, year,
  or journal are wrong.
- ⛔ **Fabricated** — the reference (and/or its DOI) doesn't exist anywhere.

`citecheck` resolves each citation's identity (DOI → Crossref/DataCite/OpenAlex/
Semantic Scholar; arXiv id → arXiv; otherwise a title search), then compares
**every asserted field** — title, first author, author set, year, venue — and
reports exactly what disagrees. Items the open APIs can't resolve are queued for
a **Google Scholar fallback** driven through a browser.

It ships with an agent **SKILL** (`skills/citation-checking/`) so Claude Code,
Codex, and similar agents use it correctly.

## Design goals

- **Zero dependencies.** Pure Python 3.8+ standard library — runs identically
  under an agent, in CI, or in a bare shell. No `pip install`.
- **Canonical-first.** Free, keyless, CAPTCHA-free APIs do the bulk of the work;
  Google Scholar is a last resort for what they miss.
- **Category-aware verdicts.** "DOI points to a different paper" is reported as a
  categorically worse problem than "year off by one".

## Install

Nothing to install. Ensure `python3` is available and run from the repo root:

```bash
python3 -m citecheck --version
```

Optionally, `pip install -e .` exposes a `citecheck` console script.

Set a contact email to use Crossref's faster "polite pool":

```bash
export CITECHECK_MAILTO="you@example.com"
```

## Usage

```bash
# BibTeX (auto-detected)
python3 -m citecheck check references.bib

# Markdown/prose document with a "References" section
python3 -m citecheck check paper.md

# Loose list of DOIs / arXiv IDs / one-line citations, or stdin
python3 -m citecheck check sources.txt
cat refs.txt | python3 -m citecheck check - --format loose

# A single citation string
python3 -m citecheck check "Vaswani et al. (2017). Attention Is All You Need. NeurIPS." --string

# Cross-check \cite keys in a .tex against the .bib
python3 -m citecheck check refs.bib --tex paper.tex

# Machine-readable output / report file
python3 -m citecheck check references.bib --format-out json
python3 -m citecheck check references.bib --format-out markdown -o report.md
```

Flags: `--strict` (default) / `--lenient`, `--no-scholar` (headless/CI),
`--fail-on-pending`, `-v/--verbose`, `--progress`.

Exit code is `1` when any `DOI_MISMATCH`, `METADATA_MISMATCH`, or `NOT_FOUND` is
found (CI-friendly), `0` otherwise.

### Google Scholar fallback

`citecheck` marks unresolved citations `NEEDS_SCHOLAR` and emits a
`scholar_query`. An agent drives Google Scholar through the **browser-act** skill
(no API exists; GS CAPTCHAs bots), scrapes the results, and feeds them back:

```bash
python3 -m citecheck scholar-verdict scraped.json   # {claim, results} -> verdict
```

See `skills/citation-checking/references/scholar-fallback.md`.

## Verdicts

| Status | Meaning |
|---|---|
| `VERIFIED` | Resolved; all fields agree |
| `MINOR_MISMATCH` | Same paper; cosmetic diff (venue abbrev, year ±1) |
| `METADATA_MISMATCH` | Right paper, wrong author/year/venue |
| `DOI_MISMATCH` | Identifier resolves to a **different** paper |
| `NOT_FOUND` | No canonical source matches (likely fabricated) |
| `NEEDS_SCHOLAR` | APIs couldn't resolve; queued for Google Scholar |
| `ERROR` | Lookup failed; inconclusive |

Full semantics: `skills/citation-checking/references/verdicts.md`.

## Layout

```
citecheck/            # the tool (stdlib only)
  models.py           # Claim / Record / Verdict
  http.py             # polite urllib client (rate-limit, retry)
  parsers.py          # BibTeX / prose / loose / LaTeX \cite
  sources.py          # Crossref, OpenAlex, Semantic Scholar, arXiv, DataCite
  matching.py         # normalization + field checks + verdict rules  <-- the core
  verify.py           # resolution pipeline
  scholar.py          # Google Scholar fallback (query + scraped-row matcher)
  report.py           # text / markdown / json
  cli.py              # `check` and `scholar-verdict`
skills/citation-checking/   # the agent SKILL + references
examples/sample.bib   # a mixed file demonstrating every verdict
tests/                # offline unit tests
```

## Credit / inspiration

- [`Galaxy-Dawn/claude-scholar`](https://github.com/Galaxy-Dawn/claude-scholar) —
  its `citation-verification` skill's layered format→existence→matching→scoring
  approach and canonical-source preference.
- [`cookjohn/gs-skills`](https://github.com/cookjohn/gs-skills) — Google Scholar
  DOM-scraping selectors and CAPTCHA-handling discipline used in the fallback.

## Testing

```bash
python3 -m unittest discover -s tests -v      # offline
python3 -m citecheck check examples/sample.bib --no-scholar   # live (network)
```
