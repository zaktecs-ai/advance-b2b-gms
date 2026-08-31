"""Website-enrichment contract tests."""
from scraper.websites.enricher import Enricher
from scraper.websites.fetcher import FetchResult


class _StubFetcher:
    def __init__(self, html: str):
        self.html = html

    def fetch(self, url: str) -> FetchResult:
        return FetchResult(url, 200, self.html, None, url, {})

    def close(self) -> None:
        pass


def test_decision_maker_flows_from_enricher(monkeypatch):
    html = "<html><body><p>John Smith, CEO of Acme Plumbing</p></body></html>"
    enricher = Enricher(use_wappalyzer=False, decision_makers=True)
    monkeypatch.setattr(enricher, "_fetcher", _StubFetcher(html))

    result = enricher.enrich("https://acme-plumbing.com")

    assert result.decision_maker_name == "John Smith"
    assert result.decision_maker_title == "CEO"


def test_decision_maker_is_opt_in(monkeypatch):
    html = "<html><body><p>John Smith, CEO of Acme Plumbing</p></body></html>"
    enricher = Enricher(use_wappalyzer=False, decision_makers=False)
    monkeypatch.setattr(enricher, "_fetcher", _StubFetcher(html))

    result = enricher.enrich("https://acme-plumbing.com")

    assert result.decision_maker_name == ""
    assert result.decision_maker_title == ""
