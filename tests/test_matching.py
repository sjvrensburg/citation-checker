"""Offline unit tests for the parsing and verdict logic (no network)."""

import unittest

from citecheck.models import Claim, Record
from citecheck.matching import (
    decide, surname, title_similarity, STRICT, LENIENT,
    VERIFIED, MINOR_MISMATCH, METADATA_MISMATCH, DOI_MISMATCH, NOT_FOUND,
)
from citecheck.parsers import (
    parse_bibtex, parse_reference_list, parse_loose, extract_cite_keys,
    parse_thebibliography, clean_latex, _guess_title,
)
from citecheck.scholar import match_scholar_results


class TestNormalization(unittest.TestCase):
    def test_surname_formats(self):
        self.assertEqual(surname("Vaswani, Ashish"), "vaswani")
        self.assertEqual(surname("Ashish Vaswani"), "vaswani")
        self.assertEqual(surname("A. Vaswani"), "vaswani")
        self.assertEqual(surname("van der Berg, J."), "van der berg")
        self.assertEqual(surname("José Peña"), "pena")

    def test_surname_particles_full_name_form(self):
        # Comma form and full-name form must normalize the same way.
        self.assertEqual(surname("Delle Monache, D."), "delle monache")
        self.assertEqual(surname("Davide Delle Monache"), "delle monache")
        self.assertEqual(surname("De Polis, A."), "de polis")
        self.assertEqual(surname("Andrea De Polis"), "de polis")

    def test_title_similarity(self):
        self.assertEqual(title_similarity("Attention Is All You Need",
                                          "attention is all you need"), 1.0)
        self.assertLess(title_similarity("Attention Is All You Need",
                                         "Deep Residual Learning"), 0.5)


class TestVerdicts(unittest.TestCase):
    def _claim(self, **kw):
        base = dict(key="k", title="Attention Is All You Need",
                    authors=["Vaswani, Ashish"], year=2017)
        base.update(kw)
        return Claim(**base)

    def test_verified(self):
        rec = Record("crossref", "doi", title="Attention Is All You Need",
                     authors=["Vaswani, Ashish", "Shazeer, Noam"], year=2017)
        self.assertEqual(decide(self._claim(doi="10.x"), rec).status, VERIFIED)

    def test_doi_points_to_different_paper(self):
        rec = Record("crossref", "doi", title="Deep Residual Learning",
                     authors=["He, Kaiming"], year=2016)
        self.assertEqual(decide(self._claim(doi="10.x"), rec).status, DOI_MISMATCH)

    def test_doi_correct_but_title_embellished(self):
        # DOI resolves; author+year+venue corroborate the same work, but the
        # cited title differs -> a title metadata error, NOT "different paper".
        claim = Claim(key="p", title="Financial Time Series and Volatility "
                      "Prediction Using NoVaS", authors=["Politis, Dimitris N."],
                      year=2009, venue="WIREs Computational Statistics", doi="10.x")
        rec = Record("crossref", "doi", title="Financial time series",
                     authors=["Dimitris N. Politis"], year=2009,
                     venue="WIREs Computational Statistics")
        v = decide(claim, rec, STRICT)
        self.assertEqual(v.status, METADATA_MISMATCH)
        self.assertIn("TITLE", " ".join(v.messages))

    def test_wrong_first_author(self):
        claim = self._claim(authors=["Smith, John"], doi="10.x")
        rec = Record("crossref", "doi", title="Attention Is All You Need",
                     authors=["Vaswani, Ashish", "Shazeer, Noam"], year=2017)
        self.assertEqual(decide(claim, rec).status, METADATA_MISMATCH)

    def test_wrong_year_major(self):
        claim = self._claim(year=2011, doi="10.x")
        rec = Record("crossref", "doi", title="Attention Is All You Need",
                     authors=["Vaswani, Ashish"], year=2017)
        self.assertEqual(decide(claim, rec).status, METADATA_MISMATCH)

    def test_year_off_by_one_verified_when_rest_matches(self):
        # Item 5: online-first vs. print drift on an otherwise perfect match
        # should verify, not demote to a mismatch — under both tolerances.
        claim = self._claim(year=2018, doi="10.x")
        rec = Record("crossref", "doi", title="Attention Is All You Need",
                     authors=["Vaswani, Ashish"], year=2017)
        self.assertEqual(decide(claim, rec, LENIENT).status, VERIFIED)
        self.assertEqual(decide(claim, rec, STRICT).status, VERIFIED)

    def test_year_off_by_one_still_minor_if_title_weak(self):
        # A borderline title match + year drift should stay a minor mismatch.
        claim = self._claim(year=2018, doi="10.x")
        rec = Record("crossref", "doi",
                     title="Attention Is All You Need for Something Else",
                     authors=["Vaswani, Ashish"], year=2017)
        self.assertEqual(decide(claim, rec, STRICT).status, MINOR_MISMATCH)

    def test_same_title_different_work_not_asserted_as_mismatch(self):
        # Item 4: title-search hit with the SAME title but different authors and
        # a far-off year is a review/citing work, not the miscited paper.
        claim = Claim(key="Embrechts1997",
                      title="Modelling Extremal Events for Insurance and Finance",
                      authors=["Embrechts, P.", "Kluppelberg, C."], year=1997)
        review = Record("openalex", "title-search",
                        title="Modelling Extremal Events for Insurance and Finance",
                        authors=["Jem N. Corcoran"], year=2002)
        self.assertEqual(decide(claim, review, STRICT).status, NOT_FOUND)

    def test_wrong_author_right_year_is_still_metadata_mismatch(self):
        # The item-4 guard must NOT swallow a genuine wrong-author citation of
        # the real paper (same title, same year, wrong author).
        claim = self._claim(authors=["Smith, John"])   # year stays 2017
        rec = Record("crossref", "title-search",
                     title="Attention Is All You Need",
                     authors=["Vaswani, Ashish", "Shazeer, Noam"], year=2017)
        self.assertEqual(decide(claim, rec, STRICT).status, METADATA_MISMATCH)

    def test_best_record_prefers_author_match_over_same_title(self):
        from citecheck.matching import best_record
        claim = Claim(key="e", title="Modelling Extremal Events",
                      authors=["Embrechts, P."], year=1997)
        book = Record("crossref", "title-search", title="Modelling Extremal Events",
                      authors=["Paul Embrechts", "Claudia Kluppelberg"], year=1997)
        review = Record("openalex", "title-search", title="Modelling Extremal Events",
                        authors=["Jem N. Corcoran"], year=2002)
        self.assertIs(best_record(claim, [review, book]), book)

    def test_weak_title_search_is_not_found(self):
        claim = self._claim()
        rec = Record("crossref", "title-search",
                     title="Totally Unrelated Paper About Bees",
                     authors=["Bee, Buzz"], year=2017)
        self.assertEqual(decide(claim, rec).status, NOT_FOUND)


class TestNameOrderAndRanking(unittest.TestCase):
    def test_family_first_record_name_is_not_a_mismatch(self):
        # Crossref book chapters return "Bollerslev Tim" (family first, no
        # comma); the trailing-token surname heuristic must not flag it.
        claim = Claim(key="b", title="Glossary to ARCH (GARCH)",
                      authors=["Bollerslev, Tim"], year=2010, doi="10.x")
        rec = Record("crossref", "doi", title="Glossary to ARCH (GARCH*)",
                     authors=["Bollerslev Tim"], year=2010)
        self.assertEqual(decide(claim, rec, STRICT).status, VERIFIED)

    def test_same_surname_different_paper_does_not_outrank_title(self):
        # A different paper by another author with the same surname must not
        # beat a perfect title match in candidate ranking.
        from citecheck.matching import best_record
        claim = Claim(key="o", title="Optuna: A Next-Generation "
                      "Hyperparameter Optimization Framework",
                      authors=["Akiba, Takuya", "Sano, Shotaro"], year=2019)
        wrong = Record("crossref", "title-search",
                       title="Motion generation of peristaltic robot by "
                             "numerical optimization framework",
                       authors=["Tomoki AKIBA", "Norihiro KAMAMICHI"], year=2021)
        right = Record("openalex", "title-search",
                       title="Optuna: A Next-generation Hyperparameter "
                             "Optimization Framework",
                       authors=["Someone Else"], year=2019)
        self.assertIs(best_record(claim, [wrong, right]), right)

    def test_preprint_year_lag_is_minor_not_metadata_mismatch(self):
        # Citing the 2021 JMLR version while the only indexed record is the
        # 2019 arXiv preprint is publication lag, not a wrong year.
        claim = Claim(key="p", title="Normalizing Flows for Probabilistic "
                      "Modeling and Inference",
                      authors=["Papamakarios, George"], year=2021,
                      venue="Journal of Machine Learning Research")
        rec = Record("openalex", "title-search",
                     title="Normalizing Flows for Probabilistic Modeling "
                           "and Inference",
                     authors=["George Papamakarios"], year=2019,
                     venue="arXiv (Cornell University)",
                     doi="10.48550/arxiv.1912.02762")
        v = decide(claim, rec, STRICT)
        self.assertEqual(v.status, MINOR_MISMATCH)
        self.assertIn("preprint", " ".join(m.lower() for m in v.messages))

    def test_earlier_cited_year_still_major_against_preprint(self):
        # The preprint-lag downgrade must not fire when the cited year is
        # EARLIER than the preprint (that's a genuinely wrong year).
        claim = Claim(key="p", title="Normalizing Flows for Probabilistic "
                      "Modeling and Inference",
                      authors=["Papamakarios, George"], year=2016)
        rec = Record("arxiv", "title-search",
                     title="Normalizing Flows for Probabilistic Modeling "
                           "and Inference",
                     authors=["George Papamakarios"], year=2019, venue="arXiv")
        self.assertEqual(decide(claim, rec, STRICT).status, METADATA_MISMATCH)


class TestGenericStubAndBooks(unittest.TestCase):
    def test_generic_registry_stub_title_is_minor(self):
        # RFS registers Engle's discussion piece with the bare title
        # "Discussion"; the conventional fuller citation is not an error.
        claim = Claim(key="e", title="Stock Volatility and the Crash of '87: "
                      "Discussion", authors=["Engle, Robert F."], year=1990,
                      venue="Review of Financial Studies", doi="10.x")
        rec = Record("crossref", "doi", title="Discussion",
                     authors=["Robert F. Engle"], year=1990,
                     venue="Review of Financial Studies")
        v = decide(claim, rec, STRICT)
        self.assertEqual(v.status, MINOR_MISMATCH)

    def test_embellished_title_still_metadata_mismatch(self):
        # A non-stub registered title that diverges stays a metadata error
        # (guarded by the existing test_doi_correct_but_title_embellished).
        pass

    def test_book_mismatch_from_title_search_defers_to_scholar(self):
        # Crossref only indexes a 1995 Technometrics *review* of the book; a
        # 20-year year gap must not be asserted as wrong metadata on the book.
        claim = Claim(key="bj", title="Time Series Analysis: Forecasting and "
                      "Control", authors=["Box, George E. P.", "Jenkins, G. M.",
                      "Reinsel, G. C.", "Ljung, G. M."], year=2015,
                      entry_type="book")
        review = Record("crossref", "title-search",
                        title="Time Series Analysis, Forecasting, and Control",
                        authors=["Eric R. Ziegel", "G. Box", "G. Jenkins",
                                 "G. Reinsel"], year=1995, venue="Technometrics")
        self.assertEqual(decide(claim, review, STRICT).status, NOT_FOUND)

    def test_book_mismatch_by_doi_still_asserted(self):
        # The book guard only applies to title-search records; a book resolved
        # by its own DOI with wrong claimed metadata is still a mismatch.
        claim = Claim(key="b", title="Some Book", authors=["Wrong, Name"],
                      year=2000, entry_type="book", doi="10.x")
        rec = Record("crossref", "doi", title="Some Book",
                     authors=["Real Author"], year=2015)
        self.assertEqual(decide(claim, rec, STRICT).status, METADATA_MISMATCH)


class TestFabricatedCoAuthor(unittest.TestCase):
    def test_invented_coauthor_on_real_paper_is_flagged(self):
        # 3 of 4 claimed surnames match (passes the overlap threshold), but the
        # invented 4th author must still surface as a minor mismatch.
        claim = Claim(key="k", title="Training Normalizing Flows from Dependent Data",
                      authors=["Kirchler, Matthias", "Khorasani, Sajad",
                               "Kloft, Marius", "Lippert, Christoph"], year=2022)
        rec = Record("arxiv", "title-search",
                     title="Training Normalizing Flows from Dependent Data",
                     authors=["Matthias Kirchler", "Christoph Lippert",
                              "Marius Kloft"], year=2022, venue="arXiv")
        v = decide(claim, rec, STRICT)
        self.assertEqual(v.status, MINOR_MISMATCH)
        self.assertIn("khorasani", " ".join(v.messages).lower())

    def test_scholar_truncated_author_line_not_flagged(self):
        # Google Scholar author lines are truncated; absent co-authors there
        # must not trigger the fabrication marker.
        claim = Claim(key="x", title="Attention Is All You Need",
                      authors=["Vaswani, Ashish", "Shazeer, Noam",
                               "Parmar, Niki"], year=2017)
        rows = [{"title": "Attention is all you need",
                 "authorline": "A Vaswani, N Shazeer",
                 "venueYear": "Advances in neural information processing systems, 2017",
                 "citedBy": "120000", "dataCid": "abc"}]
        self.assertEqual(match_scholar_results(claim, rows).status, VERIFIED)


class TestParsers(unittest.TestCase):
    def test_sici_doi_with_angle_brackets(self):
        from citecheck.parsers import find_doi
        s = ("doi:10.1002/(SICI)1097-0258(19980430)17:8"
             "<873::AID-SIM779>3.0.CO;2-I")
        self.assertEqual(find_doi(s),
                         "10.1002/(SICI)1097-0258(19980430)17:8"
                         "<873::AID-SIM779>3.0.CO;2-I")

    def test_bibtex(self):
        bib = """@article{v17, title={Attention Is All You Need},
        author={Vaswani, Ashish and Shazeer, Noam}, journal={NeurIPS},
        year={2017}, doi={10.48550/arXiv.1706.03762}}"""
        c = parse_bibtex(bib)[0]
        self.assertEqual(c.key, "v17")
        self.assertEqual(c.title, "Attention Is All You Need")
        self.assertEqual(c.authors, ["Vaswani, Ashish", "Shazeer, Noam"])
        self.assertEqual(c.year, 2017)
        self.assertEqual(c.doi, "10.48550/arXiv.1706.03762")

    def test_bibtex_arxiv_id_buried_in_journal_field(self):
        # A fabricated citation can hide a real arXiv id in a mislabeled field;
        # it must still be extracted so the id gets cross-checked.
        bib = ("@article{x, title={Some Title}, author={Fake, A.}, "
               "journal={arXiv preprint arXiv:2310.01063}, year={2024}}")
        c = parse_bibtex(bib)[0]
        self.assertEqual(c.arxiv_id, "2310.01063")

    def test_prose_numbered(self):
        text = ("References\n"
                "[1] Vaswani, A., Shazeer, N. (2017). Attention is all you need. NeurIPS.\n"
                "[2] He, K. (2016). Deep residual learning. CVPR.")
        claims = parse_reference_list(text)
        self.assertEqual(len(claims), 2)
        self.assertEqual(claims[0].year, 2017)
        self.assertIn("attention", (claims[0].title or "").lower())

    def test_loose_identifiers(self):
        claims = parse_loose("10.1038/nature14539\narXiv:1706.03762")
        self.assertEqual(claims[0].doi, "10.1038/nature14539")
        self.assertIsNone(claims[0].title)
        self.assertEqual(claims[1].arxiv_id, "1706.03762")

    def test_latex_cite_keys(self):
        tex = r"Text \citep{a,b} and \cite[p.~3]{c} and \textcite{a}."
        self.assertEqual(extract_cite_keys(tex), ["a", "b", "c"])

    def test_bare_year_title_extraction(self):
        # "Authors, YEAR. Title. Venue." — the dominant \bibitem style.
        e = ("Embrechts, P., Kluppelberg, C., Mikosch, T., 1997. "
             "Modelling Extremal Events for Insurance and Finance. Springer.")
        self.assertEqual(_guess_title(e),
                         "Modelling Extremal Events for Insurance and Finance")

    def test_clean_latex(self):
        self.assertEqual(clean_latex(r"Kl\"{u}ppelberg"), "Kluppelberg")
        self.assertEqual(clean_latex(r"G\'{o}mez"), "Gomez")
        self.assertEqual(clean_latex(r"Statistics \& Data"), "Statistics & Data")

    def test_thebibliography(self):
        tex = (r"\begin{thebibliography}{00}" "\n"
               r"\bibitem[Delle Monache et al.(2024)]{DelleMonache2024}" "\n"
               "Delle Monache, D., De Polis, A., Petrella, I., 2024. "
               "Modeling and forecasting macroeconomic downside risk. "
               "Journal of Business \\& Economic Statistics 42, 1010--1025.\n"
               r"\bibitem[G\'{o}mez et al.(2007)]{Gomez2007}" "\n"
               "G\\'{o}mez, H.W., Venegas, O., 2007. Skew-symmetric distributions. "
               "Environmetrics 18, 395--407.\n"
               r"\end{thebibliography}")
        claims = parse_thebibliography(tex)
        self.assertEqual([c.key for c in claims],
                         ["DelleMonache2024", "Gomez2007"])
        dm = claims[0]
        self.assertEqual(dm.title,
                         "Modeling and forecasting macroeconomic downside risk")
        self.assertEqual(dm.year, 2024)
        self.assertEqual(surname(dm.authors[0]), "delle monache")
        # LaTeX accents cleaned in the second entry's authors.
        self.assertEqual(surname(claims[1].authors[0]), "gomez")


class TestScholarFallback(unittest.TestCase):
    def test_scholar_match(self):
        claim = Claim(key="x", title="Attention Is All You Need",
                      authors=["Vaswani, Ashish"], year=2017)
        rows = [{"title": "Attention is all you need",
                 "authorline": "A Vaswani, N Shazeer",
                 "venueYear": "Advances in neural information processing systems, 2017",
                 "citedBy": "120000", "dataCid": "abc"}]
        self.assertEqual(match_scholar_results(claim, rows).status, VERIFIED)

    def test_scholar_citation_tag_stripped_from_title(self):
        # Citation-only rows title as "[CITATION][C] …" — the tag must not
        # depress title similarity.
        claim = Claim(key="rm", title="RiskMetrics --- Technical Document",
                      authors=["J.P. Morgan/Reuters"], year=1996)
        rows = [{"title": "[CITATION][C] RiskMetrics—Technical Document",
                 "authorline": "JP Morgan/Reuters", "venueYear": "1996",
                 "citedBy": "33", "dataCid": "x"}]
        self.assertEqual(match_scholar_results(claim, rows).status, VERIFIED)

    def test_scholar_trailing_annotation_stripped(self):
        claim = Claim(key="cox", title="Statistical Analysis of Time Series: "
                      "Some Recent Developments", authors=["Cox, David R."],
                      year=1981)
        rows = [{"title": "Statistical analysis of time series: Some recent "
                          "developments [with discussion and reply]",
                 "authorline": "DR Cox, G Gudmundsson",
                 "venueYear": "Scandinavian Journal of Statistics, 1981",
                 "citedBy": "300", "dataCid": "y"}]
        self.assertEqual(match_scholar_results(claim, rows).status, VERIFIED)

    def test_scholar_no_results_is_not_found(self):
        claim = Claim(key="x", title="A Fabricated Paper", year=2021)
        self.assertEqual(match_scholar_results(claim, []).status, NOT_FOUND)


if __name__ == "__main__":
    unittest.main()
