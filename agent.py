"""
agent.py

The FitFindr planning loop. Orchestrates the three tools in response to a
natural language user query, passing state between them via a session dict.

Complete tools.py and test each tool in isolation before implementing this file.

Usage (once implemented):
    from agent import run_agent
    from utils.data_loader import get_example_wardrobe

    result = run_agent(
        query="vintage graphic tee under $30, size M",
        wardrobe=get_example_wardrobe(),
    )
    print(result["fit_card"])
    print(result["error"])   # None on success
"""

import re

from tools import search_listings, suggest_outfit, create_fit_card


# ── query parsing ─────────────────────────────────────────────────────────────

# Price phrases like "under $30", "below 40", "less than $25.50", or a bare "$30".
_PRICE_RE = re.compile(
    r"(?:under|below|less than|max|<=?|around|about)\s*\$?\s*(\d+(?:\.\d+)?)"
    r"|\$\s*(\d+(?:\.\d+)?)",
    re.IGNORECASE,
)
# Explicit "size M" / "size 8.5" / "size W30".
_SIZE_LABELLED_RE = re.compile(r"\bsize\s+([a-z0-9./]+)", re.IGNORECASE)
# Bare clothing sizes used as standalone words (XXS..XXL).
_SIZE_BARE_RE = re.compile(r"\b(xxs|xs|xl|xxl|s|m|l)\b", re.IGNORECASE)


def _parse_query(query: str) -> dict:
    """
    Extract a search description, optional size, and optional max_price from a
    free-text user query using regex.

    Returns a dict: {"description": str, "size": str | None, "max_price": float | None}

    Chosen approach: regex (not the LLM) so parsing is deterministic, instant,
    and free — documented in planning.md.
    """
    text = query or ""
    size: str | None = None
    max_price: float | None = None

    # 1. Price — first numeric match from any of the price phrasings.
    price_match = _PRICE_RE.search(text)
    if price_match:
        raw = price_match.group(1) or price_match.group(2)
        try:
            max_price = float(raw)
        except (TypeError, ValueError):
            max_price = None
        text = text[: price_match.start()] + " " + text[price_match.end():]

    # 2. Size — prefer an explicit "size X" label, else a bare S/M/L/XL token.
    size_match = _SIZE_LABELLED_RE.search(text)
    if size_match:
        size = size_match.group(1).upper()
        text = text[: size_match.start()] + " " + text[size_match.end():]
    else:
        bare = _SIZE_BARE_RE.search(text)
        if bare:
            size = bare.group(1).upper()
            text = text[: bare.start()] + " " + text[bare.end():]

    # 3. Whatever remains is the description keywords.
    description = re.sub(r"\s+", " ", text).strip(" ,.")
    return {"description": description, "size": size, "max_price": max_price}


# ── session state ─────────────────────────────────────────────────────────────

def _new_session(query: str, wardrobe: dict) -> dict:
    """
    Initialize and return a fresh session dict for one user interaction.

    The session dict is the single source of truth for everything that happens
    during a run — it stores the original query, parsed parameters, tool results,
    and any error that caused early termination.

    You may add fields to this dict as needed for your implementation.
    """
    return {
        "query": query,              # original user query
        "parsed": {},                # extracted description / size / max_price
        "search_results": [],        # list of matching listing dicts
        "selected_item": None,       # top result, passed into suggest_outfit
        "wardrobe": wardrobe,        # user's wardrobe dict
        "outfit_suggestion": None,   # string returned by suggest_outfit
        "fit_card": None,            # string returned by create_fit_card
        "error": None,               # set if the interaction ended early
    }


# ── planning loop ─────────────────────────────────────────────────────────────

def run_agent(query: str, wardrobe: dict) -> dict:
    """
    Main agent entry point. Runs the FitFindr planning loop for a single
    user interaction and returns the completed session dict.

    Args:
        query:    Natural language user request
                  (e.g., "vintage graphic tee under $30, size M")
        wardrobe: User's wardrobe dict — use get_example_wardrobe() or
                  get_empty_wardrobe() from utils/data_loader.py

    Returns:
        The session dict after the interaction completes. Check session["error"]
        first — if it is not None, the interaction ended early and the other
        output fields (outfit_suggestion, fit_card) will be None.

    TODO — implement this function using the planning loop you designed in planning.md:

        Step 1: Initialize the session with _new_session().

        Step 2: Parse the user's query to extract a description, size, and
                max_price. You can use regex, string splitting, or ask the LLM
                to parse it — document your choice in planning.md.
                Store the result in session["parsed"].

        Step 3: Call search_listings() with the parsed parameters.
                Store results in session["search_results"].
                If no results: set session["error"] to a helpful message and
                return the session early. Do NOT proceed to suggest_outfit
                with empty input.

        Step 4: Select the item to use (e.g., the top result).
                Store it in session["selected_item"].

        Step 5: Call suggest_outfit() with the selected item and wardrobe.
                Store the result in session["outfit_suggestion"].

        Step 6: Call create_fit_card() with the outfit suggestion and selected item.
                Store the result in session["fit_card"].

        Step 7: Return the session.

    Before writing code, complete the Planning Loop and State Management sections
    of planning.md — your implementation should match what you described there.
    """
    # Step 1 — fresh session for this interaction.
    session = _new_session(query, wardrobe)

    # Guard: an empty query has nothing to act on.
    if not query or not query.strip():
        session["error"] = (
            "Tell me what you're looking for — e.g. 'vintage graphic tee under "
            "$30, size M'."
        )
        return session

    # Step 2 — parse the query into search parameters.
    session["parsed"] = _parse_query(query)
    parsed = session["parsed"]

    # Step 3 — search listings. Branch on the result.
    session["search_results"] = search_listings(
        description=parsed["description"],
        size=parsed["size"],
        max_price=parsed["max_price"],
    )
    if not session["search_results"]:
        # Error path: no matches. Build a helpful, specific message and STOP —
        # do not call suggest_outfit with empty input.
        hints = []
        if parsed["max_price"] is not None:
            hints.append(f"raising your ${parsed['max_price']:g} budget")
        if parsed["size"]:
            hints.append(f"dropping the size '{parsed['size']}' filter")
        hints.append("using more general keywords")
        session["error"] = (
            f"I couldn't find any listings matching '{parsed['description'] or query}'. "
            f"Try {', or '.join(hints)}."
        )
        return session

    # Step 4 — select the top (most relevant) result.
    session["selected_item"] = session["search_results"][0]

    # Step 5 — suggest an outfit from the selected item + wardrobe.
    session["outfit_suggestion"] = suggest_outfit(
        session["selected_item"], session["wardrobe"]
    )

    # Step 6 — turn the outfit into a shareable fit card.
    session["fit_card"] = create_fit_card(
        session["outfit_suggestion"], session["selected_item"]
    )

    # Step 7 — done.
    return session


# ── CLI test ──────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    from utils.data_loader import get_example_wardrobe, get_empty_wardrobe

    print("=== Happy path: graphic tee ===\n")
    session = run_agent(
        query="looking for a vintage graphic tee under $30",
        wardrobe=get_example_wardrobe(),
    )
    if session["error"]:
        print(f"Error: {session['error']}")
    else:
        print(f"Found: {session['selected_item']['title']}")
        print(f"\nOutfit: {session['outfit_suggestion']}")
        print(f"\nFit card: {session['fit_card']}")

    print("\n\n=== No-results path ===\n")
    session2 = run_agent(
        query="designer ballgown size XXS under $5",
        wardrobe=get_example_wardrobe(),
    )
    print(f"Error message: {session2['error']}")
