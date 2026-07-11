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
   *different paper* ⇒ `DOI_MISMATCH` — unless author, year, and venue all
   corroborate the same work, in which case only the *title* is wrong
   (`METADATA_MISMATCH`), or the registered title is a generic stub contained
   in the cited title (`MINOR_MISMATCH`; see notes).
3. **Title-search path.** With no usable identifier, search Crossref (+ author),
   OpenAlex, Semantic Scholar, and arXiv (ML/stats conference papers often exist
   *only* there); pick the best candidate by title, nudged by author/year
   agreement — the author nudge is scaled by title similarity so a same-surname
   different paper can never outrank a near-exact title match. A weak
   best-match ⇒ `NOT_FOUND` / `NEEDS_SCHOLAR`.
4. **Field agreement.** On the confirmed same paper, check first author, author
   set, year, and venue.

## Statuses

| Status | Trigger | Severity |
|---|---|---|
| `VERIFIED` | Resolved and all present fields agree | none |
| `MINOR_MISMATCH` | Same paper; diffs worth reviewing but not identity-breaking (year within tolerance or preprint lag, venue abbreviation, author present but not first, a claimed co-author absent from the record) | low — but read the notes: an absent co-author is a fabrication marker |
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
punctuation, whitespace-collapsed. Crossref titles are recombined with their
separately-registered subtitles ("Optuna" + "A Next-generation …"). Authors are
compared by **surname**, with nobiliary particles (van, von, de, …) kept
attached; the first-author surname must appear in the record. Records that
return names in "Family Given" order without a comma ("Bollerslev Tim" — common
for Crossref book chapters) are handled by a token-run fallback rather than
flagged.

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
- **Preprint publication lag:** when the resolved record is an arXiv preprint
  (JMLR-style venues assign no Crossref DOI, so the preprint is often the only
  indexed record) and the cited year is *later* with title/authors clean, the
  year gap is reported as `MINOR_MISMATCH` with an explanation — the citation
  likely refers to the published version.
- **Fabricated co-author marker:** a claimed co-author who is absent from the
  canonical record is surfaced as a `MINOR_MISMATCH` even when the overall
  author-overlap threshold passes — AI-invented author lists often graft one
  fake name onto a real paper. (Google Scholar rows are exempt: their author
  lines are truncated, not authoritative.) Given-name-only errors that keep the
  surname and initial ("Manuel" vs. "Matthias" Kirchler) are **invisible** to
  surname matching — spot-check first authors of critical references manually.
- **Same-titled different work:** a title-search hit whose authors are entirely
  absent *and* whose year is far off (e.g. a book and a later *review* sharing a
  title) is not asserted as a mismatch. Ranking weights first-author agreement
  heavily (scaled by title similarity) so the real work beats a namesake; if
  only the namesake is found, the citation is reported unresolved
  (`NOT_FOUND` / `NEEDS_SCHOLAR`), not wrong.
- **Books, manuals, and tech reports** are poorly indexed: a title search often
  lands on a journal *review* of the book, another edition, or a CRAN package
  DOI stamped with the first-release year. Field disagreements from a
  title-search record on a `@book`/`@manual`/`@techreport` entry are therefore
  never asserted as mismatches — the item is deferred to the Scholar fallback.
- **Generic registry stub titles:** publishers register discussion pieces,
  comments, errata, etc. under bare stubs ("Discussion" for Engle's RFS piece).
  When the stub appears inside the fuller cited title and author/year/venue
  corroborate, this is reported as a `MINOR_MISMATCH` metadata quirk, not an
  error.
