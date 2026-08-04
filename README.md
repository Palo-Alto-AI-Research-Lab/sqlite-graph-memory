# sqlite-graph-memory

**Graph RAG on SQLite for AI agents — a working pilot, not a framework.**

The extracted memory layer of a personal "second brain" agent setup: three small Python
scripts — `index_notes.py`, `brain_ask.py`, `turnstate_hook.py` — that give an LLM agent
*associative* recall over a folder of markdown notes.
SQLite is the only database ([`schema.sql`](schema.sql)) and `[[wikilinks]]` are the graph.

Status: **pilot**, and the word is load-bearing: it runs daily in one real setup (a ~100k-note
Obsidian vault driven by Claude Code), but it is deliberately minimal, has no tests, and makes
no attempt to be general — see [What's intentionally missing](#whats-intentionally-missing).
Published as the companion code for an upcoming write-up on lightweight Graph RAG for agents;
the one design note that is already written is [`docs/bitemporal.md`](docs/bitemporal.md).

## Why

Most Graph RAG stacks assume a graph database, an ETL pipeline, and an entity-extraction pass —
HippoRAG ([arXiv:2405.14831](https://arxiv.org/abs/2405.14831)) derives its KG that way, and
Graphiti/Zep maintain a temporal one.
For a single-user agent over a markdown knowledge base, all three are overkill:

- The **graph already exists** — Obsidian/Logseq/plain-markdown users hand-curate their
  edges as `[[wikilinks]]`, and `brain_ask.py` just parses them.
  No extraction pass, and edge quality beats anything a model would mine.
- **SQLite is enough** — the only things worth persisting are derived state (a per-turn
  ledger) and telemetry, and both are defined in [`schema.sql`](schema.sql).
  One file, stdlib driver, zero ops.
- The expensive part of RAG quality is a **reranker**, not graph infrastructure — see the
  rerank step in `brain_ask.py`.

So the pilot's bet: *vector retrieval for entry points, hand-curated wikilinks for
association, a cross-encoder to keep serendipity honest, SQLite for everything that must
persist.* All four steps live in one file, `brain_ask.py`, in that order.

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

[`schema.sql`](schema.sql) documents both tables. The graph is **not** materialized in SQL:
`brain_ask.py` parses edges from the notes at query time, bounded to 1 hop / 40 neighbours.
That keeps the graph permanently in sync with the notes at zero maintenance cost; materialize
an edge table only when hop depth or corpus size demands it.

### Design choices that survived contact with reality

- **Graph expansion is candidate generation, not ranking** (`brain_ask.py`). Neighbours go
  into the same reranker pool as vector hits, so an irrelevant linked note gets buried.
  That is what makes 1-hop expansion safe to leave on.
- **Entity gate** (`looks_like_entity` in `brain_ask.py`). A/B telemetry showed graph
  expansion helps *theme* queries ("approaches to agent memory") but hurts *name/tool*
  lookups ("NotebookLM"): a name's card links to everything, so the hop pulls in noise.
  The heuristic in `brain_ask.py` is deliberately conservative, and switches the graph off
  only for name-shaped queries.
- **Crash-safety as a policy.** The graph hop (`brain_ask.py`) and the Stop-hook
  (`turnstate_hook.py`) are both wrapped, so any failure degrades to the previous behavior:
  vector-only recall, no ledger row.
  A memory layer must never make the agent worse than having no memory layer.
- **Measure, don't believe.** `--ab` runs both pipelines on every real query and logs how
  many notes the graph promoted into the top-N, to `ab_recall` in [`schema.sql`](schema.sql).
  Whether "associative memory" earns its keep is a weekly SQL query, not a vibe.

### What's intentionally missing

- No entity lane. The production setup has an extra retrieval lane over people/project
  cards; it is too entangled with personal data to publish.
- No incremental indexing, no eval suite, no packaging, and no tests — the status line
  above says **pilot** on purpose.

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

To enable the per-turn ledger in Claude Code, register `turnstate_hook.py` as a Stop hook
(the exact block is `examples/claude-code-stop-hook.json`), then:

```bash
python turnstate_show.py --stats
```

Configuration is env-only and every variable is listed in `.env.example`; everything
defaults to the current directory. GPU is used automatically if a CUDA torch build is
present, and CPU works fine for small corpora.

### What `--ab` looks like

Output of `brain_ask.py` with `--ab`, on a toy 4-note corpus (real runs use a ~100k-note vault):

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

On a corpus this small the graph adds nothing — every note is already in the candidate
pool. The interesting deltas appear at scale, and that is exactly what the `ab_recall`
table in [`schema.sql`](schema.sql) accumulates evidence for.

## Roadmap

- **v0.1** (now): the pilot as it runs daily — pipeline, ledger, A/B telemetry, all in
  `brain_ask.py` and `turnstate_hook.py`.
- **v0.2**: a public benchmark — a synthetic 200–500 note mini-vault with real wikilinks,
  ~200 hand-labeled queries stratified by type (entity / theme / bridge / compare /
  temporal / navigational), and a full ablation matrix (hops × seed caps × neighbour caps
  × rerank pool × gating policy). The interesting question is not "does graph help" but
  *for which query classes*. Not built yet — this line is a plan, not a result.
- **Bi-temporal edges** — design note [`docs/bitemporal.md`](docs/bitemporal.md). When you
  materialize the graph instead of parsing it at query time, give every edge a validity
  window (`valid_from` / `valid_to` / `observed_at`) so a rebuild *closes* superseded facts
  instead of deleting them: history is kept, recall prefers the present, and `as_of` queries
  can reconstruct the past. Non-destructive aging by confidence decay. The day-1 A/B result
  — context size ~unchanged, retrieved candidates ~60% fresher by mean age — is written up
  with its caveats in `docs/bitemporal.md`: the volume win is longitudinal, so that is one
  day on one corpus, not a benchmark.
- A write-up on the pattern ("Graph RAG without graph extraction") is in progress.

## Models

- Embeddings: `intfloat/multilingual-e5-base` (multilingual; the home corpus is RU+EN)
- Reranker: `cross-encoder/mmarco-mMiniLMv2-L12-H384-v1`

Both are set as constants (`E5_MODEL`, `RERANK_MODEL`) in `brain_ask.py` and small enough
to run on a laptop GPU; swap freely, since nothing in the pipeline depends on these
specific models.

## Cite this work

If this repo shows up in your research, cite it via [CITATION.cff](CITATION.cff) (GitHub's "Cite this repository" button). Academic identity: Anton Dzyatkovsky publishes as **Anton Dziatkovskii** ([ORCID 0000-0001-7408-3054](https://orcid.org/0000-0001-7408-3054)).

## AI contributors

This project is built by a human + AI team, and the git log says so: Claude
writes most of the code, Codex and Grok review it, Gemini feeds the research.
Each is credited on a commit **only if its output changed that commit's
content** — no decorative credits. Lab-wide policy, one source for every repo:
[AI-CONTRIBUTORS.md](https://github.com/Palo-Alto-AI-Research-Lab/.github/blob/main/AI-CONTRIBUTORS.md).

## License

MIT

## Contact

Questions, war stories, or you want to run this on your own fleet:

- 💬 WhatsApp: **+1 341 222 9178**
- 🐦 X: [@Tony_Stef_](https://x.com/Tony_Stef_)
- 📣 Telegram: [@ClawRus](https://t.me/ClawRus) (RU) · [@ClawEng](https://t.me/ClawEng) (EN)
- 🌐 [palo-alto.ai](https://palo-alto.ai) · [Palo Alto AI Research Lab](https://github.com/Palo-Alto-AI-Research-Lab)

## Contributors welcome — and there is a queue

Issues labelled [`accepted`](https://github.com/Palo-Alto-AI-Research-Lab/sqlite-graph-memory/issues?q=is%3Aissue+is%3Aopen+label%3Aaccepted)
are scoped, free to take, and nobody is on them. Comment **"claiming this"** — no permission needed —
and it is yours for 7 days. New here? Start with
[`good first issue`](https://github.com/Palo-Alto-AI-Research-Lab/sqlite-graph-memory/issues?q=is%3Aissue+is%3Aopen+label%3A%22good+first+issue%22).

**You keep the copyright to your code.** No CLA, no assignment, ever — your contribution goes in
under this repo's existing license, the same terms as ours. We answer every issue and PR within
48 hours, including "no, and here is why"; our silence is our bug, so ping the thread.

Full deal: [CONTRIBUTING.md](https://github.com/Palo-Alto-AI-Research-Lab/.github/blob/main/CONTRIBUTING.md)
