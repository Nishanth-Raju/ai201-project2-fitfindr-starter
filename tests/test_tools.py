"""
tests/test_tools.py

Pytest coverage for the three FitFindr tools, with at least one test per
failure mode. Run from the repo root with:

    pytest tests/

The two LLM-backed tools (suggest_outfit, create_fit_card) call Groq, so the
tests that exercise them are skipped automatically when GROQ_API_KEY is not set.
"""

import os

import pytest

from tools import search_listings, suggest_outfit, create_fit_card
from utils.data_loader import get_example_wardrobe, get_empty_wardrobe

needs_groq = pytest.mark.skipif(
    not os.environ.get("GROQ_API_KEY"),
    reason="GROQ_API_KEY not set — skipping live LLM tests",
)


# ── search_listings ───────────────────────────────────────────────────────────

def test_search_returns_results():
    results = search_listings("vintage graphic tee", size=None, max_price=50)
    assert isinstance(results, list)
    assert len(results) > 0
    # Every result is a full listing dict.
    assert all("title" in item and "price" in item for item in results)


def test_search_empty_results():
    # Failure mode: nothing matches → empty list, NOT an exception.
    results = search_listings("designer ballgown", size="XXS", max_price=5)
    assert results == []


def test_search_price_filter():
    results = search_listings("jacket", size=None, max_price=40)
    assert all(item["price"] <= 40 for item in results)


def test_search_size_filter_is_token_aware():
    # "8" must match "US 8" but must NOT spuriously match "W28".
    results = search_listings("boots", size="8", max_price=None)
    for item in results:
        assert "8" in str(item["size"]).lower()
        assert "w28" not in str(item["size"]).lower()


def test_search_results_sorted_by_relevance():
    results = search_listings("vintage denim jacket", size=None, max_price=None)
    assert len(results) > 0
    # Top result should mention something from the query in title/tags.
    top = results[0]
    blob = (top["title"] + " " + " ".join(top["style_tags"])).lower()
    assert any(kw in blob for kw in ("vintage", "denim", "jacket"))


# ── suggest_outfit ────────────────────────────────────────────────────────────

@needs_groq
def test_suggest_outfit_with_wardrobe():
    item = search_listings("vintage graphic tee", size=None, max_price=50)[0]
    out = suggest_outfit(item, get_example_wardrobe())
    assert isinstance(out, str)
    assert len(out.strip()) > 0


@needs_groq
def test_suggest_outfit_empty_wardrobe():
    # Failure mode: empty wardrobe → still returns useful, non-empty advice.
    item = search_listings("vintage graphic tee", size=None, max_price=50)[0]
    out = suggest_outfit(item, get_empty_wardrobe())
    assert isinstance(out, str)
    assert len(out.strip()) > 0


def test_suggest_outfit_no_item():
    # Defensive: missing item should not raise.
    out = suggest_outfit({}, get_example_wardrobe())
    assert isinstance(out, str)
    assert len(out.strip()) > 0


# ── create_fit_card ───────────────────────────────────────────────────────────

@needs_groq
def test_create_fit_card_returns_caption():
    item = search_listings("vintage graphic tee", size=None, max_price=50)[0]
    card = create_fit_card("Pair it with baggy jeans and combat boots.", item)
    assert isinstance(card, str)
    assert len(card.strip()) > 0


def test_create_fit_card_empty_outfit():
    # Failure mode: empty outfit → descriptive error string, NOT an exception.
    item = search_listings("vintage graphic tee", size=None, max_price=50)[0]
    card = create_fit_card("", item)
    assert isinstance(card, str)
    assert len(card.strip()) > 0
    assert "fit card" in card.lower() or "outfit" in card.lower()


def test_create_fit_card_whitespace_outfit():
    item = search_listings("vintage graphic tee", size=None, max_price=50)[0]
    card = create_fit_card("   \n  ", item)
    assert isinstance(card, str)
    assert len(card.strip()) > 0
