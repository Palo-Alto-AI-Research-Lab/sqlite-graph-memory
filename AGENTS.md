# AGENTS.md — working in this repo

Written for AI coding agents, and equally readable by a human contributor. Short on purpose.

## What this repo is

The extracted memory layer of a personal second-brain agent setup: vector retrieval for entry
points, hand-curated `[[wikilinks]]` for association, a cross-encoder rerank, and SQLite for the
only two things worth persisting — a per-turn ledger and A/B telemetry.

**Status: pilot.** It runs daily in one real setup and it is deliberately minimal. It is not a
framework and should not grow into one. The interesting claim here is what it *does not* need: no
graph database, no ETL, no entity-extraction pass.

## Stack and layout

- **Python + SQLite (stdlib driver).** `requirements.txt` covers the embedding/rerank models only.
- `index_notes.py` — chunk + embed a markdown folder (e5-base) into a `.npy`/`.pkl` index.
- `brain_ask.py` — the recall pipeline: dense retrieve → optional `--graph` 1-hop wikilink
  expansion → cross-encoder rerank → top-12. `--ab` runs vector-only *and* vector+graph, diffs
  them, and logs the delta to SQLite.
- `turnstate_hook.py` — agent Stop-hook; appends one row per assistant turn. Zero tokens, pure
  stdlib. `turnstate_show.py` is the read-only viewer.
- `schema.sql` — documents both tables (`turns`, `ab_recall`).
- `examples/claude-code-stop-hook.json` — how the hook gets wired.

## How to verify a change

There is **no test suite yet** — that is a known gap with an open issue, and closing it is welcome
work. Until then, a change is verified by running it and pasting the output:

```bash
python index_notes.py <folder-of-markdown>     # build an index over a small sample
python brain_ask.py "<question>" --graph       # recall, with graph expansion
python brain_ask.py "<question>" --ab          # both arms + the logged delta
python turnstate_show.py                       # what the ledger captured
```

Use a **synthetic** notes folder — five or six files with a couple of `[[wikilinks]]` between them
is enough to show a behaviour change. Never paste real notes into an issue or PR.

If you touch retrieval, `--ab` is the honest way to show your change helps: it produces a measured
delta, not an opinion.

## Conventions

- **The graph is not materialized in SQL.** Edges are parsed from the notes at query time, bounded
  to 1 hop and 40 neighbours. That keeps the graph in sync with the notes at zero maintenance cost.
  Materializing an edge table is a real option — but only when hop depth or corpus size demands it,
  and with the measurement that shows it.
- **The turn ledger costs zero tokens.** Anything that puts a model call in the hook path is
  rejected on principle: it runs after *every* turn.
- Stdlib and boring SQL. One file, no ORM, no migrations framework.
- The indexer must ignore what is not a note: editor state, sync-conflict copies, versioned
  backups. Silently indexing five copies of one file is a bug we have already had.

## Boundaries — what needs a human

- **Turning the pilot into a framework** — plugin systems, abstraction layers, a config format.
  Open an issue; the answer is usually "not yet, and here is why".
- **Changing the embedding or reranker model.** It invalidates every existing index; needs a
  measured before/after and a note in the README.
- **Schema changes** to `turns` or `ab_recall` — existing data lives in those tables.

## The deal

Your copyright stays yours, there is no CLA, and issues labelled `accepted` are free to take —
comment "claiming this". Full terms:
[CONTRIBUTING.md](https://github.com/Palo-Alto-AI-Research-Lab/.github/blob/main/CONTRIBUTING.md).

If an AI wrote your change, say so in the PR and confirm you ran it. Welcome here — we do it daily.
Unread generated code is the one thing that gets closed on sight.
