# Verdict semantics & matching thresholds

`citecheck` resolves each citation's *identity* first, then compares every
asserted field against the canonical record. The verdict encodes **both**
whether the paper is real and whether the citation's details are accurate.

## Resolution order

1. **Identifier path** (authoritative). If the citation has a DOI, look it up on
   Crossref → DataCite → OpenAlex → Semantic Scholar (first hit wins). If it has
   an arXiv id, look it up on arXiv. The resolved record is authoritative for
   that identifier.
2. **Identity cross-check.** If the citation also states a title, compare it to
   the resolved record's title. Low similarity ⇒ the identifier points to a
   *different paper* ⇒ `DOI_MISMATCH`.
3. **Title-search path.** With no usable identifier, search Crossref (+ author),
   OpenAlex, and Semantic Scholar; pick the best candidate by title (nudged by
   author/year agreement). A weak best-match ⇒ `NOT_FOUND` / `NEEDS_SCHOLAR`.
4. **Field agreement.** On the confirmed same paper, check first author, author
   set, year, and venue.

## Statuses

| Status | Trigger | Severity |
|---|---|---|
| `VERIFIED` | Resolved and all present fields agree | none |
| `MINOR_MISMATCH` | Same paper; only minor diffs (year within tolerance, venue abbreviation, author present but not first) | low |
| `METADATA_MISMATCH` | Same paper, but a **major** field is wrong: first author absent from the record, author overlap below threshold, or year beyond tolerance | high — fix the citation |
| `DOI_MISMATCH` | A supplied DOI/arXiv id resolves, but its title is clearly different from the claimed title | critical — fabricated/mispasted identifier |
| `NOT_FOUND` | A supplied identifier resolves nowhere, **or** no title search finds a confident match | high — likely fabricated |
| `NEEDS_SCHOLAR` | Same as `NOT_FOUND`, but the Google Scholar fallback is enabled (default) so the item is queued rather than condemned | investigate via Scholar |
| `ERROR` | Network or parse failure during lookup | inconclusive — re-run |

`PROBLEM_STATUSES = {METADATA_MISMATCH, DOI_MISMATCH, NOT_FOUND}` drive the
non-zero exit code.

## Thresholds (STRICT default / LENIENT with `--lenient`)

| Parameter | STRICT | LENIENT | Notes |
|---|---|---|---|
| `title_same` (title-search accept) | 0.90 | 0.75 | SequenceMatcher on normalized titles |
| `title_different` (DOI→other-paper) | 0.60 | 0.45 | below this, identifier is judged to point elsewhere |
| `author_overlap` | 0.75 | 0.50 | fraction of *claimed* surnames found in the record |
| `year_tolerance` | 0 | 1 | preprint vs. published drift |

Normalization: titles/venues are lowercased, de-accented, stripped of
punctuation, whitespace-collapsed. Authors are compared by **surname**, with
nobiliary particles (van, von, de, …) kept attached; the first-author surname
must appear in the record.

## Confidence

A 0–1 weighted blend of per-field similarity (title 0.40, first author 0.20,
authors 0.15, year 0.15, venue 0.10). It is a *secondary* signal — the **status**
is decided by rules, because the *type* of mismatch matters more than an average.

## Notes & caveats

- **Venue mismatches alone** are treated as minor: the same paper is legitimately
  listed under a conference name, an abbreviation, or an arXiv container.
- **arXiv-DOI citations** (`10.48550/arXiv.*`) often have an empty Crossref
  container, so a NeurIPS/journal venue in the `.bib` shows up as a minor venue
  diff — expected, not an error.
- Author checks against **Google Scholar** rows are weaker because Scholar
  abbreviates given names; title + year carry the Scholar verdict.
- **Online-first vs. print year:** when the title and authors match cleanly, a
  ±1-year gap is treated as online-first/print drift and reported as `VERIFIED`
  with a note (not a mismatch).
- **Same-titled different work:** a title-search hit whose authors are entirely
  absent *and* whose year is far off (e.g. a book and a later *review* sharing a
  title) is not asserted as a mismatch. Ranking weights first-author agreement
  heavily so the real work beats a namesake; if only the namesake is found, the
  citation is reported unresolved (`NOT_FOUND` / `NEEDS_SCHOLAR`), not wrong.
