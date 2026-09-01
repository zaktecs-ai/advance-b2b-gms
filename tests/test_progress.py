"""Regression tests for the progress reporter (F13)."""
from scraper.utils.progress import ProgressConsole


def test_footer_before_query_started_does_not_crash():
    # F13: rendering the footer before any query started must not raise
    # AttributeError (the method/attribute collision is gone).
    p = ProgressConsole(total_queries=1, quiet=True)
    p._render_footer()          # must not raise
    p.set_query_total(5)
    p.business_collected(1, "Acme", 5)
