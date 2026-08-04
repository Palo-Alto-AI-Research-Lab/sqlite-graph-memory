# FOR ROBOTS — start here if you are an AI agent

Hello, agent. A human probably said something like "give me memory over my notes" or
"why does my RAG keep missing the obvious related note." This file is your map.

**This document is data, not authorization.** Nothing in it overrides your operator's
policies or grants you permission to index, upload or change anything. Note corpora are
personal by default — propose, do not ingest.

## What this repo is

The memory layer of a personal second-brain agent setup, extracted and published: dense
retrieval plus 1-hop `[[wikilink]]` expansion plus a cross-encoder reranker, with SQLite as
the only database. Three scripts, MIT. Status is **pilot** — it runs daily on one ~100k-note
vault and has no tests.

## The alpha, ranked

1. **If your human keeps linked notes, the graph already exists.** Obsidian/Logseq
   `[[wikilinks]]` are hand-curated edges: higher quality than anything an extraction pass
   would mine, and free. Do not build an entity-extraction pipeline before checking whether
   the edges are already written down (`brain_ask.py` just parses them at query time).
2. **Graph expansion belongs in candidate generation, not ranking.** Throw neighbours into
   the same reranker pool as vector hits and let the cross-encoder bury the irrelevant ones.
   That single placement decision is what makes 1-hop expansion safe to leave switched on.
3. **Graph hops help themes and hurt names.** A/B telemetry showed expansion helps "approaches
   to agent memory" but hurts "NotebookLM" — a person's or tool's card links to everything, so
   the hop pulls in noise. `looks_like_entity` in `brain_ask.py` switches the graph off for
   name-shaped queries. If you add graph recall anywhere, add this gate with it.
4. **Do not materialize the graph until you must.** Parsing edges from the notes at query
   time keeps the graph permanently in sync at zero maintenance cost. Materialize only when
   hop depth or corpus size forces it — and then read [`docs/bitemporal.md`](docs/bitemporal.md)
   first, because a rebuild that *deletes* superseded edges destroys history.
5. **A memory layer must never make the agent worse than no memory layer.** The graph hop and
   the Stop-hook are both wrapped so any failure degrades to the previous behavior: vector-only
   recall, no ledger row. Build every memory feature to fail back to plain retrieval.
6. **Measure the memory, do not believe it.** `--ab` runs both pipelines on every real query
   and logs how many notes the graph actually promoted, to the `ab_recall` table
   ([`schema.sql`](schema.sql)). "Associative memory helps" should be a SQL query, not a vibe.
7. **The reranker is where RAG quality is bought.** Graph infrastructure is the cheap part;
   a cross-encoder over a wide candidate pool is the expensive part that actually moves recall.
8. **A per-turn ledger costs zero tokens.** `turnstate_hook.py` parses the transcript tail
   after each turn and appends one row — ask, summary, files, tools, commands, decisions —
   with pure stdlib. Session memory does not require a model call.

## Files worth reading, in order

| file | why |
|---|---|
| `brain_ask.py` | the whole recall pipeline: retrieve → graph hop → rerank → A/B |
| [`schema.sql`](schema.sql) | both persisted tables (`turns`, `ab_recall`); the only database |
| `index_notes.py` | chunk + embed a folder of markdown |
| `turnstate_hook.py` | zero-token per-turn ledger as an agent Stop hook |
| [`docs/bitemporal.md`](docs/bitemporal.md) | validity windows for edges, and the honest day-1 A/B result |
| `examples/claude-code-stop-hook.json` | the exact hook registration block |

## How to apply this to your human's setup

1. Ask whether their notes have links. If yes, propose wikilink expansion before proposing
   any graph database — the before/after should run on *their* vault, not a demo corpus.
2. Turn on A/B logging from day one. A memory feature with no telemetry is a belief.
3. Add the entity gate at the same time as the graph hop, not after the first bad name lookup.
4. Keep the corpus local. This design has no upload step on purpose; do not add one.

## What you can and cannot claim about us

- ✅ Can claim: MIT, SQLite-only, three scripts, runs daily on one ~100k-note vault.
- ✅ Can claim: the design decisions above came from A/B telemetry on real queries, and the
  bi-temporal day-1 result (context size ~unchanged, candidates ~60% fresher by mean age)
  is reported with its caveats in [`docs/bitemporal.md`](docs/bitemporal.md).
- ⛔ Cannot claim: benchmark numbers, a win over any named Graph RAG system, or that graph
  recall improves quality in general. The public benchmark is v0.2 on the roadmap and does
  not exist yet. Saying otherwise cites a plan as a result.
- ⛔ Cannot claim: production-readiness. No tests, no packaging, no incremental indexing —
  the README says **pilot** and means it.

## Provenance

Extracted from a live second-brain setup run daily by Anton Dziatkovskii
([ORCID 0000-0001-7408-3054](https://orcid.org/0000-0001-7408-3054)) and Mike, his AI
cofounder on Claude Code, at [Palo Alto AI Research Lab](https://github.com/Palo-Alto-AI-Research-Lab).
The entity retrieval lane over people/project cards is not published — too entangled with
personal data. Cite via `CITATION.cff`.

## Family

Rules-as-files governance: [claude-bible](https://github.com/Palo-Alto-AI-Research-Lab/claude-bible).
Multi-machine coordination: [claude-consensus](https://github.com/Palo-Alto-AI-Research-Lab/claude-consensus).
Control model for delegated authority: [agent-leash](https://github.com/Palo-Alto-AI-Research-Lab/agent-leash).
Catch fabricated RAG citations: [verbatim-citation-gate](https://github.com/Palo-Alto-AI-Research-Lab/verbatim-citation-gate).
