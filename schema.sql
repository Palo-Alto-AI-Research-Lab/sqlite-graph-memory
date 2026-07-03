-- SQLite schema for the graph-memory pilot.
-- One file (turnstate.db by default) holds both the per-turn ledger and the
-- retrieval A/B telemetry. Both tables are auto-created by the scripts;
-- this file is documentation, not a required migration.

-- Per-turn semantic state ledger (written by turnstate_hook.py after every
-- assistant turn; 0 LLM tokens, pure transcript parsing).
CREATE TABLE IF NOT EXISTS turns(
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id TEXT,          -- agent session id
    ts TEXT,                  -- wall-clock 'YYYY-MM-DD HH:MM:SS'
    cwd TEXT,                 -- working directory of the session
    project TEXT,             -- basename(cwd), cheap grouping key
    ask TEXT,                 -- last real user message in the turn (clipped)
    summary TEXT,             -- assistant text of the turn (clipped)
    files TEXT,               -- JSON array: files touched via Write/Edit tools
    tools TEXT,               -- JSON array: tool names used this turn
    commands TEXT,            -- JSON array: shell commands run (clipped)
    decisions TEXT,           -- JSON array: lines matching the decision regex
    evidence TEXT,            -- path to the raw transcript (.jsonl) = source of truth
    byte_start INTEGER,       -- transcript byte range this row was derived from
    byte_end INTEGER
);
CREATE INDEX IF NOT EXISTS ix_turns_sid ON turns(session_id);

-- Retrieval A/B telemetry (written by brain_ask.py --ab).
-- Each row = one real query answered by BOTH pipelines:
--   A "Direct"      = vector retrieve -> rerank
--   B "Associative" = vector + 1-hop wikilink graph expansion -> rerank
CREATE TABLE IF NOT EXISTS ab_recall(
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts TEXT,
    query TEXT,
    cand_added INTEGER,           -- graph neighbours added to the candidate pool
    new_in_top INTEGER,           -- notes in B's top-N that A's top-N missed
    promoted_via_graph INTEGER,   -- of those, how many arrived via the graph hop
    promoted_titles TEXT,         -- '; '-joined titles (clipped) for eyeballing
    source TEXT                   -- 'work' = live usage, or a batch/eval label
);

-- The graph itself is deliberately NOT materialized in SQL in this pilot:
-- edges are [[wikilinks]] parsed from the markdown notes at query time
-- (bounded: 1 hop from top-15 hits, max 40 neighbours). Zero maintenance,
-- always in sync with the notes. Materialize an edges(src, dst) table only
-- when/if hop depth or corpus size makes query-time parsing too slow.
