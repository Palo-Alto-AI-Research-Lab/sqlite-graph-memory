# sqlite-graph-memory

**Graph RAG on SQLite for AI agents — a working pilot, not a framework.**

This repo is the extracted memory layer of a personal "second brain" agent setup:
a few small Python scripts that give an LLM agent *associative* recall over a folder
of markdown notes, with SQLite as the only database and `[[wikilinks]]` as the graph.

Status: **pilot**. It runs daily in one real setup (a ~100k-note Obsidian vault driven
by Claude Code), but it is deliberately minimal, has no tests, and makes no attempt to
be general. Published as the companion code for an upcoming write-up on lightweight
Graph RAG for agents.

## Why

Most Graph RAG stacks assume a graph database, an ETL pipeline, and an entity-extraction
pass. For a single-user agent over a markdown knowledge base, all three are overkill:

- The **graph already exists** — people who keep linked notes (Obsidian, Logseq, plain
  markdown) have hand-curated edges: `[[wikilinks]]`. No extraction needed, and edge
  quality is higher than anything a model would mine.
- **SQLite is enough** — the only things worth persisting are derived state
  (a per-turn ledger) and telemetry (does graph expansion actually help?). One file,
  stdlib driver, zero ops.
- The expensive part of RAG quality is a **reranker**, not graph infrastructure.

So the pilot's bet: *vector retrieval for entry points, hand-curated wikilinks for
association, a cross-encoder to keep serendipity honest, SQLite for everything that
must persist.*

## Architecture

Three layers, three failure-isolated components:

```
markdown notes (with [[wikilinks]])
        │
        ├── index_notes.py      chunk + embed (e5-base) → .npy/.pkl index
        │
        ├── brain_ask.py        recall pipeline:
        │       1. dense retrieve  top-60 chunks, dedup by file
        │       2. --graph         +1-hop wikilink neighbours of top-15 hits (max 40)
        │       3. rerank          cross-encoder → top-12
        │       4. --ab            run vector-only AND vector+graph, diff,
        │                          log the delta to SQLite (ab_recall)
        │
        └── turnstate_hook.py   agent Stop-hook: after every assistant turn,
                                parse the transcript tail and append one row
                                (ask, summary, files, tools, commands, decisions)
                                to SQLite (turns). 0 LLM tokens, pure stdlib.
                                turnstate_show.py = read-only viewer.
```

`schema.sql` documents both tables. The graph is **not** materialized in SQL:
edges are parsed from the notes at query time, bounded to 1 hop / 40 neighbours.
That keeps the graph permanently in sync with the notes at zero maintenance cost;
materialize an edge table only when hop depth or corpus size demands it.

### Design choices that survived contact with reality

- **Graph expansion is candidate generation, not ranking.** Neighbours are thrown
  into the same reranker pool as vector hits. If a linked note isn't actually
  relevant to the query, the cross-encoder buries it. This is what makes 1-hop
  expansion safe to leave on.
- **Entity gate.** A/B telemetry showed graph expansion helps *theme* queries
  ("approaches to agent memory") but hurts *name/tool* lookups ("NotebookLM") —
  a name's card links to everything, so the hop pulls in noise. `looks_like_entity()`
  is a conservative heuristic that switches the graph off for name-shaped queries.
- **Crash-safety as a policy.** The graph hop and the Stop-hook are both wrapped so
  any failure degrades to the previous behavior (vector-only recall; no ledger row).
  A memory layer must never make the agent worse than having no memory layer.
- **Measure, don't believe.** `--ab` runs both pipelines on every real query and logs
  how many notes the graph promoted into the top-N. Whether "associative memory" earns
  its keep is a weekly SQL query, not a vibe.

### What's intentionally missing

- No entity lane (the production setup has an extra retrieval lane over people/project
  cards; it is too entangled with personal data to publish).
- No incremental indexing, no eval suite, no packaging. Pilot.

## Quickstart

```bash
pip install -r requirements.txt

# 1. index a folder of markdown notes
python index_notes.py /path/to/notes

# 2. ask, three ways
python brain_ask.py "how do I think about agent memory"
python brain_ask.py --graph "how do I think about agent memory"
python brain_ask.py --ab    "how do I think about agent memory"   # logs the diff to SQLite

# 3. inspect the A/B telemetry
sqlite3 turnstate.db "select ts, query, new_in_top, promoted_via_graph from ab_recall order by id desc limit 10"
```

To enable the per-turn ledger in Claude Code, register `turnstate_hook.py` as a
Stop hook (see `examples/claude-code-stop-hook.json`), then:

```bash
python turnstate_show.py --stats
```

Configuration is env-only; see `.env.example`. Everything defaults to the current
directory. GPU is used automatically if a CUDA torch build is present; CPU works fine
for small corpora.

### What `--ab` looks like

On a toy 4-note corpus (real runs use a ~100k-note vault):

```
A/B RECALL: how should agent memory work
(Direct=4 vector / Associative=+0 graph; Associative promoted 0 new notes into top-12, 0 of them via graph)

--- DIRECT memory (vector) ---
 1. [  4.11] agent-memory
 2. [ -6.50] graph-rag
 3. [ -8.66] sqlite-ledger
 4. [ -9.73] cooking

--- ASSOCIATIVE memory (vector+graph) ---
 1. [  4.11] agent-memory
 2. [ -6.50] graph-rag
 3. [ -8.66] sqlite-ledger
 4. [ -9.73] cooking
```

On a corpus this small the graph adds nothing (every note is already in the
candidate pool) — the interesting deltas appear at scale, and that is exactly
what the `ab_recall` table accumulates evidence for.

## Roadmap

- **v0.1** (now): the pilot as it runs daily — pipeline, ledger, A/B telemetry.
- **v0.2**: a public benchmark — a synthetic 200–500 note mini-vault with real
  wikilinks, ~200 hand-labeled queries stratified by type (entity / theme /
  bridge / compare / temporal / navigational), and a full ablation matrix
  (hops × seed caps × neighbour caps × rerank pool × gating policy). The
  interesting question is not "does graph help" but *for which query classes*.
- **Bi-temporal edges** (design note: [`docs/bitemporal.md`](docs/bitemporal.md)) —
  when you materialize the graph instead of parsing it at query time, give every edge
  a validity window (`valid_from` / `valid_to` / `observed_at`) so a rebuild *closes*
  superseded facts instead of deleting them: history is kept, recall prefers the
  present, and `as_of` queries can reconstruct the past. Non-destructive aging by
  confidence decay. Day-1 A/B: ~60% fresher recall; the volume win is longitudinal.
- A write-up on the pattern ("Graph RAG without graph extraction") is in progress.

## Models

- Embeddings: `intfloat/multilingual-e5-base` (multilingual; the home corpus is RU+EN)
- Reranker: `cross-encoder/mmarco-mMiniLMv2-L12-H384-v1`

Both are small enough to run on a laptop GPU; swap freely — nothing in the pipeline
depends on these specific models.

## License

MIT
