# FitFindr — planning.md

> Spec written before implementation. Updated as the design firmed up.

---

## Tools

### Tool 1: search_listings

**What it does:**
Searches the 40-item mock listings dataset for pieces matching the user's keywords, with optional size and price-ceiling filters, and returns them ranked by how relevant they are to the query.

**Input parameters:**
- `description` (str): free-text keywords describing the desired item, e.g. `"vintage graphic tee"`. Tokenized; stopwords are dropped.
- `size` (str | None): a size to filter by, e.g. `"M"` or `"8"`. Matched as a whole token (case-insensitive) so `"M"` matches `"S/M"` and `"8"` matches `"US 8"` but not `"W28"`. `None` skips the filter.
- `max_price` (float | None): inclusive price ceiling. `None` skips the filter.

**What it returns:**
A `list[dict]` of full listing dicts (`id`, `title`, `description`, `category`, `style_tags`, `size`, `condition`, `price`, `colors`, `brand`, `platform`), sorted by keyword-overlap score, best match first. Listings with a score of 0 are dropped.

**What happens if it fails or returns nothing:**
Returns an empty list `[]` — never raises. The agent detects the empty list and stops with a helpful error message (see Error Handling); it does not call `suggest_outfit`.

---

### Tool 2: suggest_outfit

**What it does:**
Given a selected listing and the user's wardrobe, asks the LLM to propose 1–2 complete outfits that pair the new item with specific pieces the user already owns.

**Input parameters:**
- `new_item` (dict): the listing the user is considering (a `search_listings` result).
- `wardrobe` (dict): a wardrobe dict with an `items` list (each item has `name`, `category`, `colors`, `style_tags`, `notes`). May be empty.

**What it returns:**
A non-empty `str` of outfit suggestions referencing the user's pieces by name.

**What happens if it fails or returns nothing:**
- Empty wardrobe → returns general styling advice for the item instead of naming pieces the user doesn't have.
- Missing `new_item` or an LLM/network error → returns a graceful fallback string. Never raises, never returns `""`.

---

### Tool 3: create_fit_card

**What it does:**
Turns an outfit suggestion plus the item details into a short, shareable, casual social-media caption (an "OOTD" post).

**Input parameters:**
- `outfit` (str): the suggestion string from `suggest_outfit`.
- `new_item` (dict): the listing dict (used for name, price, platform).

**What it returns:**
A 2–4 sentence caption `str`. Uses a high LLM temperature so the output varies across runs and inputs.

**What happens if it fails or returns nothing:**
- Empty / whitespace-only `outfit` → returns a descriptive error string telling the user to suggest an outfit first.
- Missing `new_item` or an LLM error → returns a graceful fallback string. Never raises.

---

### Additional Tools (if any)

None for the core submission. (Candidate stretch tool: `estimate_price_fairness` comparing against same-category listings.)

---

## Planning Loop

**How does your agent decide which tool to call next?**

The loop is condition-driven, not a fixed sequence:

1. If the query is empty → set `session["error"]` and return immediately. No tools run.
2. Parse the query into `description` / `size` / `max_price` (regex).
3. Call `search_listings`. **Branch on the result:**
   - If the result list is **empty** → set a specific `session["error"]` and **return early**. `suggest_outfit` and `create_fit_card` are never called.
   - If non-empty → set `selected_item = results[0]` and continue.
4. Call `suggest_outfit(selected_item, wardrobe)` → store `outfit_suggestion`.
5. Call `create_fit_card(outfit_suggestion, selected_item)` → store `fit_card`.
6. Return the session.

The agent is "done" when it either hits an early-return error path or completes step 5 with a populated `fit_card`. Because steps 4–6 only run when step 3 found a match, the agent's behavior genuinely differs based on what `search_listings` returns.

---

## State Management

**How does information from one tool get passed to the next?**

A single `session` dict (created by `_new_session`) is the source of truth for one interaction. It holds: `query`, `parsed`, `search_results`, `selected_item`, `wardrobe`, `outfit_suggestion`, `fit_card`, and `error`. Each step writes its output into the session, and the next step reads from it:

- `search_listings` output → `session["search_results"]` → `session["selected_item"] = results[0]`
- `selected_item` → input to `suggest_outfit` → output to `session["outfit_suggestion"]`
- `outfit_suggestion` + `selected_item` → input to `create_fit_card` → `session["fit_card"]`

The user never re-enters the item; it flows from search → outfit → fit card via the session dict. The completed session is returned and `app.py` maps its fields onto the three UI panels.

---

## Error Handling

| Tool | Failure mode | Agent response |
|------|-------------|----------------|
| search_listings | No results match the query | Returns `[]`; the loop sets a specific error naming the query and suggesting concrete fixes (raise budget / drop size filter / broaden keywords) and returns early without calling other tools. |
| suggest_outfit | Wardrobe is empty | Returns general styling advice for the item (colors, silhouettes, occasions) instead of naming nonexistent pieces; never empty, never raises. |
| create_fit_card | Outfit input is missing or incomplete | Returns a descriptive error string ("I can't write a fit card without an outfit suggestion…") rather than raising or returning `""`. |

Additionally, both LLM tools wrap the Groq call in try/except and return a graceful fallback string on any API/network error.

---

## Architecture

```
User query (+ wardrobe choice)
        │
        ▼
   Planning Loop (run_agent) ─────────────────────────────────────┐
        │                                                          │
        │  empty query? ──► [ERROR] "Tell me what you're..." → return
        │                                                          │
        ├─► _parse_query(query) ─► session["parsed"]              │
        │        (description, size, max_price)                    │
        │                                                          │
        ├─► search_listings(description, size, max_price)          │
        │        │ results == []                                   │
        │        ├──► [ERROR] "No listings found..." → return ─────┤
        │        │                                                 │
        │        │ results == [item, ...]                          │
        │        ▼                                                 │
        │   session["selected_item"] = results[0]                 │
        │        │                                                 │
        ├─► suggest_outfit(selected_item, wardrobe)                │
        │        │   (empty wardrobe → general advice)             │
        │   session["outfit_suggestion"] = "..."                  │
        │        │                                                 │
        └─► create_fit_card(outfit_suggestion, selected_item)      │
                 │   (empty outfit → error string)                 │
            session["fit_card"] = "..."                            │
                 │                          error paths return here┘
                 ▼
         Return session  ──►  app.py maps to 3 UI panels
```

State store: the `session` dict carries `parsed`, `search_results`, `selected_item`, `outfit_suggestion`, `fit_card`, and `error` between every step.

---

## AI Tool Plan

**Milestone 3 — Individual tool implementations:**
I used Claude (Claude Code). For `search_listings` I gave it the Tool 1 spec (the three params, the ranked-list return, and the empty-list failure mode) plus a sample listing dict, and asked it to implement scoring with `load_listings()`. I verified it filtered by all three parameters and returned `[]` (not an exception) on no match before trusting it — and caught/fixed a bug where size `"8"` matched `"W28"` (switched to word-boundary matching). For `suggest_outfit` and `create_fit_card` I gave it the Tool 2/3 specs and required: empty-wardrobe fallback, empty-outfit guard returning a string, and a high temperature so cards vary. I verified by running each tool in isolation and confirming two runs of `create_fit_card` on identical input produced different captions.

**Milestone 4 — Planning loop and state management:**
I gave Claude the Planning Loop + State Management sections and the architecture diagram above, and asked it to implement `run_agent` following the numbered TODO steps. I verified the generated loop branches on the `search_listings` result (early return on `[]`) and does **not** call all three tools unconditionally, and that every output is read from / written to the `session` dict rather than recomputed.

---

## A Complete Interaction (Step by Step)

**Example user query:** "I'm looking for a vintage graphic tee under $30. I mostly wear baggy jeans and chunky sneakers. What's out there and how would I style it?"

**Step 1:** `run_agent` initializes the session, then `_parse_query` extracts `description="vintage graphic tee"`, `size=None`, `max_price=30.0` and stores them in `session["parsed"]`.

**Step 2:** `search_listings("vintage graphic tee", None, 30.0)` runs. It returns several scored matches; the top result is stored as `session["search_results"]`, and `session["selected_item"]` is set to `results[0]` (e.g. the "Y2K Baby Tee — Butterfly Print", $18, depop). Because the list is non-empty, the loop proceeds.

**Step 3:** `suggest_outfit(selected_item, example_wardrobe)` runs. It formats the wardrobe and asks the LLM for outfits, returning e.g. "Pair it with your baggy straight-leg jeans and chunky white sneakers…" stored in `session["outfit_suggestion"]`.

**Step 4:** `create_fit_card(outfit_suggestion, selected_item)` runs with high temperature, returning a caption like "just thrifted this y2k baby tee off depop for $18 🦋 styled with my baggies + chunky sneakers…" stored in `session["fit_card"]`.

**Final output to user:**
Three populated panels in the Gradio UI — the selected listing (title, price, size, condition, platform, style, description), the outfit idea, and the shareable fit card. If instead the search had returned nothing (e.g. "designer ballgown size XXS under $5"), only the first panel would show an error explaining what to try differently, and the other two would be blank.
