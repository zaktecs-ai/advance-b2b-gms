"""Filter engine: AND/OR/NOT, two-pass split, operators."""
from scraper.filters.engine import evaluate, split_filters


def _rec(**kw):
    base = {"website": "https://a.com", "review_count": 20, "rating": 4.5,
            "emails": "a@a.com", "city": "Dallas"}
    base.update(kw)
    return base


def test_equality():
    assert evaluate(_rec(), {"include_all": [{"city": "Dallas"}]})[0] is True
    assert evaluate(_rec(), {"include_all": [{"city": "Austin"}]})[0] is False


def test_and():
    f = {"include_all": [{"city": "Dallas"}, {"reviews": 20, "op": ">="}]}
    assert evaluate(_rec(), f)[0] is True
    f = {"include_all": [{"city": "Dallas"}, {"reviews": 30, "op": ">="}]}
    assert evaluate(_rec(), f)[0] is False


def test_string_condition_form():
    f = {"include_all": ["reviews >= 15"]}
    assert evaluate(_rec(), f)[0] is True
    f = {"include_all": ["reviews >= 30"]}
    assert evaluate(_rec(), f)[0] is False


def test_or():
    f = {"include_any": [{"city": "Austin"}, {"review_count": 20}]}
    assert evaluate(_rec(), f)[0] is True


def test_exclude():
    f = {"exclude_all": [{"city": "Dallas"}]}
    assert evaluate(_rec(), f)[0] is False
    f = {"exclude_any": [{"city": "Austin"}]}
    assert evaluate(_rec(), f)[0] is True


def test_operator_in():
    f = {"include_all": [{"city": ["Dallas", "Austin"], "op": "in"}]}
    assert evaluate(_rec(), f)[0] is True
    f = {"include_all": [{"city": ["Dallas", "Austin"], "op": "notin"}]}
    assert evaluate(_rec(), f)[0] is False


def test_operator_neq():
    f = {"include_all": [{"city": "Dallas", "op": "!="}]}
    assert evaluate(_rec(), f)[0] is False


def test_negate():
    f = {"include_all": [{"city": "Austin", "negate": True}]}
    assert evaluate(_rec(), f)[0] is True


def test_split_filters():
    filters = {
        "include_all": [{"city": "Dallas"}, {"email_found": "yes"}],
        "exclude_any": [{"website_status": "DEAD"}],
    }
    pre, post = split_filters(filters)
    assert "include_all" in pre and "exclude_any" in post
    assert any("email_found" in c for c in post.get("include_all", []))
    assert any("city" in c for c in pre.get("include_all", []))
