# Bi-temporal edges: giving a rebuilt graph a memory

> Design note for the persisted-edge variant of the pilot. The base pilot parses
> `[[wikilinks]]` at query time and never stores edges (see `schema.sql`). Once you
> *do* materialize edges (entity co-occurrence, mention counts, anything derived from
> a changing corpus), you hit a problem the query-time version doesn't have: **rot**.

## The problem

A graph derived from a folder of notes is usually rebuilt from scratch on a schedule
(`DROP`, re-scan, re-insert). That rebuild is stateless: it only ever knows *the
current corpus*. So:

- When a note is edited and a link disappears, the edge silently vanishes on the next
  rebuild. You can't ask *"when did this stop being true?"* or *"what was true last
  month?"* — the history was never kept.
- Every edge looks equally fresh. A co-occurrence first seen two years ago and one
  seen yesterday rank identically, because "when did we observe this" isn't recorded.

For a personal agent memory this is exactly backwards: you want it to *prefer current
truth* and *keep a defensible history*, not flatten both.

## The approach: close, don't delete

Borrowed from bi-temporal databases and temporal knowledge-graph work (Graphiti / Zep,
HippoRAG). Every edge and mention row carries a validity window plus provenance:

```sql
CREATE TABLE mentions(
  eid INTEGER, path TEXT, cnt INTEGER,
  valid_from  TEXT,   -- when this fact was first observed (ISO-8601 UTC)
  valid_to    TEXT,   -- when it disappeared from the corpus; NULL = still open
  observed_at TEXT,   -- freshness signal: mtime of the source note
  source_id   TEXT,   -- provenance: which note produced this row
  confidence  REAL    -- extraction confidence / aging weight, 0..1
);
CREATE TABLE rel(         -- entity <-> entity co-occurrence edge
  a INTEGER, b INTEGER, cnt INTEGER,
  valid_from TEXT, valid_to TEXT, observed_at TEXT, source_id TEXT, confidence REAL
);
CREATE INDEX ix_m_open ON mentions(valid_to);   -- fast "open windows" filter
CREATE INDEX ix_r_open ON rel(valid_to);
```

The rebuild stops being a `DROP`+re-insert and becomes a **diff-upsert** against the
open rows:

| current scan vs open rows | action |
|---|---|
| edge **still present** | keep the window open, refresh `observed_at`/`cnt` *only if changed* |
| edge **gone** | set `valid_to = now` — **close the window, never delete** |
| edge **new** (or reappeared after a close) | insert a fresh open window |

History accumulates instead of being overwritten. Row counts are monotonic; a nightly
run that changes nothing writes nothing.

### One gotcha: stable identity

If entity ids are assigned by insertion order (`id = row_number`), they are *not stable*
across rebuilds, so you can't match "the same edge" from one night to the next. Resolve
ids by a **natural key** instead — the source-card path for known entities, the
lowercased display string for link targets — and reuse the prior id when the key matches,
only minting new ids (`max(id)+1`) for genuinely new entities. Then `(a, b)` and
`(eid, path)` are stable keys the diff can reconcile against.

## Temporal-aware recall

With windows in place, retrieval gets two new modes at ~zero cost:

- **prefer the present** — filter to `valid_to IS NULL` and order by `observed_at DESC`,
  so fresh evidence surfaces above stale evidence of equal link-strength.
- **time travel** — pass an `as_of` timestamp and filter
  `valid_from <= as_of AND (valid_to IS NULL OR valid_to > as_of)` to reconstruct what
  the graph asserted at a past date (useful for "what did I think about X in May?").

Both degrade safely: any failure falls back to the untimed lane, so recall never breaks.

## Aging without deleting

Pruning is **ranking, not destruction**. Two non-destructive levers, run nightly:

1. **Confidence decay** — recompute `confidence` for open edges from the age of
   `observed_at` (e.g. `<=30d → 1.0`, `30–90d → 0.9`, … `>1y → 0.6`), change-only so it
   rewrites nothing on a quiet night. Downstream ranking can multiply by it.
2. **Archive closed windows** — move long-closed rows (`valid_to` older than a
   retention horizon) into `*_archive` tables. They leave the active query path but the
   audit trail is preserved.

Closed windows are already invisible to "prefer the present" recall, so staleness stops
polluting context the moment an edge closes — the archive step is just housekeeping to
keep the active DB lean over years.

## Provenance

Every row links `source_id` + `observed_at` + `confidence`, and the recall output is
validated against a schema (a small Pydantic model here) before it is trusted — so every
retrieved candidate can cite where and when it came from, and malformed rows are dropped
rather than silently ranked.

## Honest day-1 result

The volume win from bi-temporal edges is **longitudinal** — it only shows up once history
accrues and stale windows start closing (weeks, not day one). On a freshly-built graph
where every window is still open, an A/B of "prefer the present" vs untimed recall over a
few dozen live queries showed what you'd expect:

- context size ~unchanged (nothing has closed yet),
- but retrieved candidates **~60% fresher on average** (mean age of returned notes
  roughly halved), and the top of the ranking reshuffled toward recent evidence on
  essentially every query.

So the freshness reordering pays off immediately; the staleness/bloat reduction is a
slope you measure weekly, not a day-one number. Worth stating plainly rather than
quoting an optimistic headline.

## References

- Graphiti / Zep — temporal knowledge graph with validity intervals (open source).
- HippoRAG (arXiv:2405.14831) — personalized-PageRank retrieval over a derived KG.
