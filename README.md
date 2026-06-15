# FitFindr 🛍️

A multi-tool AI thrift-shopping agent. The user describes what they want in
plain language; FitFindr searches a dataset of secondhand listings, styles the
best match against the user's wardrobe, and writes a shareable "fit card"
caption — passing state between every step and degrading gracefully when a tool
returns nothing useful.

Built for CodePath AI201, Week 2, Project 2.

---

## Setup

```bash
python -m venv .venv
source .venv/Scripts/activate   # Windows (Git Bash);  .venv\Scripts\activate on cmd
pip install -r requirements.txt
```

Create a `.env` file in the repo root (it's gitignored — never commit it):

```
GROQ_API_KEY=your_key_here
```

Get a free key at [console.groq.com](https://console.groq.com). Then run:

```bash
python app.py          # launch the Gradio UI (URL is printed in the terminal)
python agent.py        # CLI smoke test: happy path + no-results path
pytest tests/          # run the tool tests
```

The LLM is Groq's `llama-3.3-70b-versatile` (free tier).

---

## Tool Inventory

| Tool | Inputs | Output | Purpose |
|------|--------|--------|---------|
| `search_listings` | `description` (str), `size` (str \| None), `max_price` (float \| None) | `list[dict]` of listing dicts, ranked by relevance (empty list if none match) | Find secondhand items matching keywords, size, and budget |
| `suggest_outfit` | `new_item` (dict), `wardrobe` (dict with `items` list) | `str` — 1–2 outfit ideas | Style the found item against the user's existing wardrobe (LLM) |
| `create_fit_card` | `outfit` (str), `new_item` (dict) | `str` — a 2–4 sentence caption | Turn the outfit into a shareable OOTD-style social caption (LLM) |

Each listing dict contains: `id`, `title`, `description`, `category`,
`style_tags` (list), `size`, `condition`, `price` (float), `colors` (list),
`brand`, `platform`. These signatures match the actual functions in
[`tools.py`](tools.py).

---

## How the Planning Loop Works

`run_agent(query, wardrobe)` in [`agent.py`](agent.py) drives the agent. It is
**condition-driven**, not a fixed pipeline — the later tools only run if the
earlier ones succeed:

1. **Empty query?** → set an error and return immediately. No tools run.
2. **Parse** the query with regex into `description`, `size`, `max_price`.
3. **Search** with `search_listings(...)`. **This is the decision point:**
   - If it returns `[]` → set a specific error message and **return early**.
     `suggest_outfit` and `create_fit_card` are *never* called on empty input.
   - Otherwise → `selected_item = results[0]` and continue.
4. **Suggest** an outfit from the selected item + wardrobe.
5. **Fit card** from the outfit + item.
6. Return the session.

Because steps 4–6 are gated on step 3 finding a match, the agent behaves
differently for a matching query vs. an impossible one — that branch is the
"planning" in the planning loop.

The query parser uses **regex rather than an LLM** (deterministic, instant,
free): it pulls a price from phrasings like "under $30", a size from "size M" or
a bare `S/M/L/XL` token, and treats the rest as search keywords.

---

## State Management

A single `session` dict (created by `_new_session`) is the source of truth for
one interaction. Each step writes its result into the session, and the next step
reads from it — the user never re-enters anything:

```
search_listings → session["search_results"] → session["selected_item"] = results[0]
selected_item   → suggest_outfit            → session["outfit_suggestion"]
outfit + item   → create_fit_card           → session["fit_card"]
```

Fields tracked: `query`, `parsed`, `search_results`, `selected_item`,
`wardrobe`, `outfit_suggestion`, `fit_card`, `error`. The completed session is
returned to `app.py`, which maps `selected_item` / `outfit_suggestion` /
`fit_card` onto the three UI panels (or shows `error` in the first panel).

You can see the item flow end-to-end: the dict produced by `search_listings` is
the exact dict passed into `suggest_outfit` and then into `create_fit_card` —
no recomputation, no re-entry.

---

## Error Handling (per tool)

| Tool | Failure mode | What the agent does |
|------|--------------|---------------------|
| `search_listings` | No matches | Returns `[]` (never raises). The loop stops and tells the user what to change — raise budget, drop the size filter, broaden keywords. |
| `suggest_outfit` | Empty wardrobe | Falls back to general styling advice for the item instead of naming pieces the user doesn't own. Also wrapped in try/except for LLM errors. |
| `create_fit_card` | Empty/whitespace outfit | Returns a descriptive error *string* ("I can't write a fit card without an outfit suggestion…"), not an exception. Also try/except for LLM errors. |

**Concrete example from testing** — running the impossible query
`"designer ballgown size XXS under $5"`:

```
search_listings("designer ballgown", size="XXS", max_price=5)  ->  []
```

The agent returns, in `session["error"]`:

> I couldn't find any listings matching 'designer ballgown'. Try raising your $5
> budget, or dropping the size 'XXS' filter, or using more general keywords.

`suggest_outfit` and `create_fit_card` are never reached, and `session["fit_card"]`
stays `None`.

---

## Spec Reflection

**One way the spec helped:** Writing the `search_listings` failure mode in
`planning.md` *before* coding ("return `[]`, never raise; the loop stops and does
not call `suggest_outfit`") made the planning-loop branch obvious — the early
return wasn't an afterthought, it was designed in.

**One way implementation diverged:** My original size filter did a plain
substring match, which the spec didn't catch as a problem. In testing, size
`"8"` spuriously matched a `"W28"` waist listing. I changed the implementation to
**whole-token (word-boundary) matching** so `"8"` matches `"US 8"` but not
`"W28"`, and updated the Tool 1 spec to say so.

---

## AI Usage

I used **Claude (via Claude Code)** throughout.

1. **`search_listings` implementation.** I gave Claude the Tool 1 spec (three
   params, ranked-list return, empty-list failure mode) plus a sample listing
   dict and asked it to implement keyword scoring on top of `load_listings()`. I
   reviewed and tested the result against several queries and **overrode the size
   matching**: its first version matched `"8"` inside `"W28"`. I had it switch to
   word-boundary matching and added a regression test for it.

2. **Planning loop in `agent.py`.** I gave Claude the Planning Loop + State
   Management sections and the architecture diagram from `planning.md` and asked
   it to implement `run_agent` per the numbered TODOs. I verified it branches on
   the `search_listings` result (early return on `[]`) and reads/writes
   everything through the `session` dict rather than recomputing — which matched
   the spec, so I kept it. I also had it make the query parser regex-based rather
   than calling the LLM, for determinism.

---

## Project Layout

```
fitfindr/
├── agent.py            # planning loop + query parser + session state
├── tools.py            # the 3 tools (search_listings, suggest_outfit, create_fit_card)
├── app.py              # Gradio UI (handle_query maps session -> 3 panels)
├── planning.md         # the spec, written before implementation
├── tests/test_tools.py # pytest coverage, one+ test per failure mode
├── data/               # listings.json (40 items) + wardrobe_schema.json
└── utils/data_loader.py# load_listings / get_example_wardrobe / get_empty_wardrobe
```
