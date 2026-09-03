"""Regression tests for the progress reporter (F13)."""
from scraper.utils.progress import ProgressConsole


def test_footer_before_query_started_does_not_crash():
    # F13: rendering the footer before any query started must not raise
    # AttributeError (the method/attribute collision is gone).
    p = ProgressConsole(total_queries=1, quiet=True)
    p._render_footer()          # must not raise
    p.set_query_total(5)
    p.business_collected(1, "Acme", 5)


def test_query_done_reports_of_n(capsys):
    # G08: query_done() must read current_query_total (written by
    # set_query_total/business_collected) so the per-query summary actually
    # shows "collected N of M".
    p = ProgressConsole(total_queries=2)
    p.query_started(1, "gyms in Dallas")
    p.set_query_total(7)
    p.business_collected(1, "Acme", 7)
    p.query_done()
    out = capsys.readouterr().out
    assert "collected 1 of 7" in out


def test_query_total_resets_between_queries(capsys):
    # G08: a new query must not inherit the previous query's total.
    p = ProgressConsole(total_queries=2)
    p.query_started(1, "gyms in Dallas")
    p.set_query_total(7)
    p.query_done()
    capsys.readouterr()  # drain query 1 output
    p.query_started(2, "dentists in Dallas")
    p.query_done()
    out = capsys.readouterr().out
    assert "of 7" not in out
    assert "collected 0" in out


def test_print_survives_non_utf8_stdout(monkeypatch):
    # Windows pipes / legacy consoles encode stdout with e.g. cp1252, whose
    # charmap cannot represent the reporter's Unicode glyphs ("━", "↳").
    # The reporter must degrade to '?' instead of raising UnicodeEncodeError.
    import io

    from scraper.utils import progress as progress_mod

    buf = io.BytesIO()
    fake = io.TextIOWrapper(buf, encoding="cp1252")
    monkeypatch.setattr(progress_mod.sys, "stdout", fake)
    p = progress_mod.ProgressConsole(total_queries=1)
    p._print("━━━ [1/2] gyms in Dallas ━━━")  # must not raise
    p.query_done()
    fake.flush()
    assert buf.getvalue()
