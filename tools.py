"""
tools.py

The three required FitFindr tools. Each tool is a standalone function that
can be called and tested independently before being wired into the agent loop.

Tools:
    search_listings(description, size, max_price)  -> list[dict]
    suggest_outfit(new_item, wardrobe)              -> str
    create_fit_card(outfit, new_item)               -> str
"""

import os
import re

from dotenv import load_dotenv
from groq import Groq

from utils.data_loader import load_listings

load_dotenv()

# Model used for the two LLM-backed tools (see requirements: free Groq tier).
MODEL = "llama-3.3-70b-versatile"

# Words that carry no search signal — dropped before keyword scoring so a query
# like "looking for a vintage tee" matches on "vintage"/"tee", not "for"/"a".
_STOPWORDS = {
    "a", "an", "the", "for", "with", "and", "or", "of", "in", "on", "to",
    "i", "im", "i'm", "am", "looking", "want", "need", "find", "me", "my",
    "some", "any", "that", "this", "is", "are", "under", "around", "about",
}


# -- Groq client ---------------------------------------------------------------

def _get_groq_client():
    """Initialize and return a Groq client using GROQ_API_KEY from .env."""
    api_key = os.environ.get("GROQ_API_KEY")
    if not api_key:
        raise ValueError(
            "GROQ_API_KEY not set. Add it to a .env file in the project root."
        )
    return Groq(api_key=api_key)


def _tokenize(text: str) -> list[str]:
    """Lowercase a string and split it into meaningful keyword tokens."""
    words = re.findall(r"[a-z0-9']+", text.lower())
    return [w for w in words if w not in _STOPWORDS and len(w) > 1]


# -- Tool 1: search_listings ---------------------------------------------------

def search_listings(
    description: str,
    size: str | None = None,
    max_price: float | None = None,
) -> list[dict]:
    """
    Search the mock listings dataset for items matching the description,
    optional size, and optional price ceiling.

    Args:
        description: Keywords describing what the user is looking for
                     (e.g., "vintage graphic tee").
        size:        Size string to filter by, or None to skip size filtering.
                     Matching is case-insensitive and fuzzy (e.g., "M" matches
                     "S/M", "8" matches "US 8").
        max_price:   Maximum price (inclusive), or None to skip price filtering.

    Returns:
        A list of matching listing dicts, sorted by relevance (best match first).
        Returns an empty list if nothing matches -- does NOT raise an exception.

    Each listing dict has the following fields:
        id, title, description, category, style_tags (list), size,
        condition, price (float), colors (list), brand, platform
    """
    listings = load_listings()
    query_tokens = _tokenize(description or "")

    size_needle = size.strip().lower() if size and size.strip() else None

    scored: list[tuple[int, dict]] = []
    for item in listings:
        # 1. Hard filter: price ceiling.
        if max_price is not None and item.get("price", 0) > max_price:
            continue

        # 2. Hard filter: size. Match the requested size as a whole token so
        #    "M" matches "S/M" and "8" matches "US 8" -- but "8" does NOT
        #    spuriously match "W28".
        if size_needle:
            item_size = str(item.get("size", "")).lower()
            if not re.search(r"\b" + re.escape(size_needle) + r"\b", item_size):
                continue

        # 3. Score by keyword overlap with the searchable text fields.
        score = _score_listing(item, query_tokens)
        if score > 0:
            scored.append((score, item))

    # 4. Highest score first. With no query tokens, everything passing the
    #    hard filters scores 0 and is dropped -- that is the intended behavior
    #    (a search needs at least one keyword to match on).
    scored.sort(key=lambda pair: pair[0], reverse=True)
    return [item for _, item in scored]


def _score_listing(item: dict, query_tokens: list[str]) -> int:
    """Score one listing by how many query keywords it matches, weighting
    title and style tags more heavily than the free-text description."""
    if not query_tokens:
        return 0

    title_tokens = set(_tokenize(item.get("title", "")))
    desc_tokens = set(_tokenize(item.get("description", "")))
    tag_tokens = {t.lower() for t in item.get("style_tags", [])}
    color_tokens = {c.lower() for c in item.get("colors", [])}
    category = str(item.get("category", "")).lower()
    brand = str(item.get("brand") or "").lower()

    score = 0
    for token in query_tokens:
        if token in title_tokens:
            score += 3
        if token in tag_tokens:
            score += 3
        if token == category or category in token:
            score += 2
        if token in color_tokens:
            score += 2
        if token in brand:
            score += 2
        if token in desc_tokens:
            score += 1
    return score


# -- Tool 2: suggest_outfit ----------------------------------------------------

def suggest_outfit(new_item: dict, wardrobe: dict) -> str:
    """
    Given a thrifted item and the user's wardrobe, suggest 1-2 complete outfits.

    Args:
        new_item: A listing dict (the item the user is considering buying).
        wardrobe: A wardrobe dict with an 'items' key containing a list of
                  wardrobe item dicts. May be empty -- handled gracefully.

    Returns:
        A non-empty string with outfit suggestions. If the wardrobe is empty,
        returns general styling advice for the item rather than raising or
        returning an empty string.
    """
    if not new_item:
        return "I couldn't put together an outfit because no item was provided."

    item_summary = _format_item(new_item)
    items = (wardrobe or {}).get("items", [])

    client = _get_groq_client()

    if not items:
        # Empty-wardrobe failure mode: fall back to general styling advice.
        prompt = (
            "A user is considering buying this secondhand item but hasn't told "
            "us anything about their existing wardrobe:\n\n"
            f"{item_summary}\n\n"
            "Give friendly, general styling advice: what kinds of pieces "
            "(colors, silhouettes, shoes) pair well with it, and what vibe or "
            "occasion it suits. Keep it to 3-4 sentences. Do NOT invent "
            "specific items the user owns -- speak in general terms."
        )
    else:
        wardrobe_text = "\n".join(f"- {_format_wardrobe_item(w)}" for w in items)
        prompt = (
            "A user is considering buying this secondhand item:\n\n"
            f"{item_summary}\n\n"
            "Here is what's already in their wardrobe:\n"
            f"{wardrobe_text}\n\n"
            "Suggest 1-2 complete outfits that pair the new item with specific "
            "pieces from their wardrobe. Refer to their pieces by name. Be "
            "concrete about how to wear it (tuck, layer, roll sleeves, etc.) "
            "and name the overall vibe. Keep it to 3-5 sentences."
        )

    try:
        response = client.chat.completions.create(
            model=MODEL,
            messages=[
                {
                    "role": "system",
                    "content": "You are a sharp, encouraging personal stylist "
                    "who knows thrift and vintage fashion.",
                },
                {"role": "user", "content": prompt},
            ],
            temperature=0.7,
        )
        text = (response.choices[0].message.content or "").strip()
        if not text:
            raise ValueError("empty response")
        return text
    except Exception as exc:  # noqa: BLE001 - degrade gracefully, never crash
        return (
            f"I couldn't generate a styling suggestion right now ({exc}). "
            f"As a starting point, {new_item.get('title', 'this piece')} works "
            "well with simple, neutral basics that let it stand out."
        )


# -- Tool 3: create_fit_card ---------------------------------------------------

def create_fit_card(outfit: str, new_item: dict) -> str:
    """
    Generate a short, shareable outfit caption for the thrifted find.

    Args:
        outfit:   The outfit suggestion string from suggest_outfit().
        new_item: The listing dict for the thrifted item.

    Returns:
        A 2-4 sentence string usable as an Instagram/TikTok caption.
        If outfit is empty or missing, returns a descriptive error message
        string -- does NOT raise an exception.
    """
    # Guard against an empty / whitespace-only outfit (the failure mode).
    if not outfit or not outfit.strip():
        return (
            "I can't write a fit card without an outfit suggestion to base it "
            "on. Try suggesting an outfit first, then generate the card."
        )
    if not new_item:
        return "I can't write a fit card without item details (name, price, platform)."

    title = new_item.get("title", "this piece")
    price = new_item.get("price")
    platform = new_item.get("platform", "a resale app")
    price_str = f"${price:g}" if isinstance(price, (int, float)) else "a steal"

    prompt = (
        "Write a short, shareable social-media caption (like an Instagram OOTD "
        "post) for a thrifted outfit. Style guidelines:\n"
        "- Casual and authentic, NOT a product description.\n"
        "- Mention the item name, price, and platform naturally, once each.\n"
        "- Capture the outfit's vibe in specific terms.\n"
        "- 2-4 sentences. Emojis are welcome but optional.\n\n"
        f"Item: {title}\n"
        f"Price: {price_str}\n"
        f"Platform: {platform}\n"
        f"Outfit: {outfit}\n\n"
        "Write only the caption."
    )

    try:
        client = _get_groq_client()
        response = client.chat.completions.create(
            model=MODEL,
            messages=[
                {
                    "role": "system",
                    "content": "You write fun, authentic-sounding fashion "
                    "captions for thrift finds.",
                },
                {"role": "user", "content": prompt},
            ],
            # Higher temperature so the caption varies across runs / inputs.
            temperature=1.0,
        )
        text = (response.choices[0].message.content or "").strip()
        if not text:
            raise ValueError("empty response")
        return text
    except Exception as exc:  # noqa: BLE001 - degrade gracefully, never crash
        return (
            f"Couldn't generate a fit card right now ({exc}). "
            f"Scored this {title} for {price_str} on {platform} -- styled it up "
            "and it's a whole vibe."
        )


# -- formatting helpers --------------------------------------------------------

def _format_item(item: dict) -> str:
    """Render a listing dict into a compact, LLM-friendly description."""
    parts = [
        f"Title: {item.get('title', 'Unknown')}",
        f"Category: {item.get('category', 'n/a')}",
        f"Style: {', '.join(item.get('style_tags', [])) or 'n/a'}",
        f"Colors: {', '.join(item.get('colors', [])) or 'n/a'}",
        f"Size: {item.get('size', 'n/a')}",
        f"Condition: {item.get('condition', 'n/a')}",
        f"Price: ${item.get('price', '?')}",
        f"Description: {item.get('description', '')}",
    ]
    return "\n".join(parts)


def _format_wardrobe_item(w: dict) -> str:
    """Render a wardrobe item dict into a one-line description."""
    name = w.get("name", "item")
    tags = ", ".join(w.get("style_tags", []))
    notes = w.get("notes")
    line = f"{name} ({w.get('category', '')})"
    if tags:
        line += f" -- {tags}"
    if notes:
        line += f" [{notes}]"
    return line
