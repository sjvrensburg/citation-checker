# Google Scholar fallback via browser-act

Use this **only** for citations `citecheck` marks `NEEDS_SCHOLAR`. The canonical
APIs (Crossref/OpenAlex/Semantic Scholar/arXiv/DataCite) are tried first because
they are fast, reliable, and never CAPTCHA. Google Scholar is a last resort for
works those APIs miss (some books, theses, non-English or grey literature).

Google Scholar has **no API** and aggressively blocks automation, so `citecheck`
never scrapes it directly. Instead, an agent drives a real browser through the
**browser-act** skill, scrapes the results, and feeds them back to `citecheck`
for a proper field-by-field verdict.

> Always invoke the `browser-act` skill before running any browser-act command.
> Never call browser-act directly via Bash.

## Step 1 — get the queue

```bash
python3 -m citecheck check references.bib --format-out json -o /tmp/cc.json
```

From the JSON, collect every result with `"status": "NEEDS_SCHOLAR"`. Each
carries a ready-made `scholar_query` and the parsed `claim` object.

## Step 2 — search Google Scholar for each item

Build the URL (or reuse `scholar_url_for` from `citecheck.scholar`):

```
https://scholar.google.com/scholar?hl=en&num=10&q=<URL-encoded scholar_query>
```

Using browser-act: open the URL in a persistent session, wait for
`#gs_res_ccl`, and extract the result rows. The DOM-scraping snippet (mirrors
the selectors used by cookjohn/gs-skills) is available as
`citecheck.scholar.SCHOLAR_SCRAPE_JS`; it returns JSON of the form:

```json
{"results": [
  {"title": "...", "authorline": "A Vaswani, N Shazeer",
   "venueYear": "Advances in neural information processing systems, 2017",
   "citedBy": "120000", "dataCid": "…", "fullTextUrl": "…"}
]}
```

If the snippet returns `{"error": "captcha"}` (or the page shows "unusual
traffic"): **stop immediately, tell the user to complete the CAPTCHA in their
browser, and wait for confirmation.** Do not auto-retry — it makes things worse.

## Step 3 — get a real verdict (don't eyeball it)

Feed the claim + scraped rows back to `citecheck` so the same rigorous field
matching is applied to the Scholar data:

```bash
# /tmp/gs.json = {"claim": {...from step 1...}, "results": [...from step 2...]}
python3 -m citecheck scholar-verdict /tmp/gs.json
```

It emits a JSON verdict (`VERIFIED` / `MINOR_MISMATCH` / `METADATA_MISMATCH` /
`NOT_FOUND`). Because Scholar abbreviates author given names, author agreement is
approximate; title and year drive the decision. A confident title+year match on
Scholar with a plausible author line ⇒ the paper is real (upgrade from
`NEEDS_SCHOLAR`). No matching row ⇒ treat as fabricated.

## Step 4 — report

Fold the Scholar verdicts back into the overall summary. For anything still
unresolved after Scholar, report it explicitly as **unverified** — never present
an unverified citation as correct.

## Pacing & etiquette

- One query at a time; pause between requests. Never fire rapid successive loads.
- Keep `num=10`. Prefer the user's existing authenticated Chrome session.
- If Scholar is unreachable or CAPTCHA-locked and the user can't help, stop and
  report the remaining items as unverified rather than guessing.
