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

    def test_year_off_by_one_is_minor(self):
        claim = self._claim(year=2018, doi="10.x")
        rec = Record("crossref", "doi", title="Attention Is All You Need",
                     authors=["Vaswani, Ashish"], year=2017)
        # LENIENT tolerates ±1 year -> not a problem
        self.assertEqual(decide(claim, rec, LENIENT).status, VERIFIED)
        # STRICT tolerance 0 -> minor
        self.assertEqual(decide(claim, rec, STRICT).status, MINOR_MISMATCH)

    def test_weak_title_search_is_not_found(self):
        claim = self._claim()
        rec = Record("crossref", "title-search",
                     title="Totally Unrelated Paper About Bees",
                     authors=["Bee, Buzz"], year=2017)
        self.assertEqual(decide(claim, rec).status, NOT_FOUND)


class TestParsers(unittest.TestCase):
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

    def test_scholar_no_results_is_not_found(self):
        claim = Claim(key="x", title="A Fabricated Paper", year=2021)
        self.assertEqual(match_scholar_results(claim, []).status, NOT_FOUND)


if __name__ == "__main__":
    unittest.main()
