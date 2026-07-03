# -*- coding: utf-8 -*-
"""brain_ask.py — vector retrieval + graph expansion + A/B harness over a markdown vault.

Pipeline: e5-base dense retrieve (top-60, dedup by file) -> optional 1-hop wikilink
graph expansion -> cross-encoder rerank -> top-12.

USAGE:
  python brain_ask.py "question"              # vector-only (Direct memory)
  python brain_ask.py --graph "question"      # vector + graph expansion (Associative)
  python brain_ask.py --ab "question"         # run BOTH, diff, log to SQLite (ab_recall)
  python brain_ask.py --ask "question"        # emit a context bundle for an LLM agent

Config (env, all optional):
  BRAIN_INDEX_DIR   dir holding the embedding index (default: ./index)
  TURNSTATE_DB      SQLite db for A/B telemetry (default: ./turnstate.db)
  BRAIN_ANSWER_OUT  file to mirror the answer into (default: ./_brain_answer.txt)
  BRAIN_AB_JSON     if set, --ab also writes a structured JSON diff to this path
"""
import os, re, sys, pickle, datetime, sqlite3, time
from pathlib import Path
import numpy as np

# Make stdout/stderr UTF-8 so non-ASCII notes never crash a Windows console.
for _s in (sys.stdout, sys.stderr):
    try: _s.reconfigure(encoding='utf-8')
    except Exception: pass

INDEX_DIR = Path(os.getenv('BRAIN_INDEX_DIR', 'index'))
AB_DB = Path(os.getenv('TURNSTATE_DB', 'turnstate.db'))
EMB = INDEX_DIR / '_brain_e5.npy'
META = INDEX_DIR / '_brain_e5_meta.pkl'
E5_MODEL = 'intfloat/multilingual-e5-base'
RERANK_MODEL = 'cross-encoder/mmarco-mMiniLMv2-L12-H384-v1'
TOPK_RETRIEVE = 60
TOPN = 12
GHOPS, GMAX = 15, 40   # graph-expansion: expand from top-GHOPS hits, add at most GMAX neighbours

# Known single-token product/tool names for the entity gate (see looks_like_entity).
# Extend with the tools/projects that appear in YOUR vault.
ENTITY_TOOLS = {'obsidian', 'syncthing', 'sqlite', 'telegram', 'whatsapp', 'n8n'}


def pick_device():
    try:
        import torch
        return 'cuda' if torch.cuda.is_available() else 'cpu'
    except Exception:
        return 'cpu'


def looks_like_entity(q):
    """True if the query is a NAME / tool / project lookup.

    Why: in our A/B runs graph expansion helped THEME queries but hurt ENTITY queries
    (a name pulls in every note that links to the person's card — mostly noise).
    CONSERVATIVE: only fires when confident; on doubt returns False so graph stays ON.
    Themes (lowercase common-noun phrases) never trip it; names/tools (capitalised /
    CamelCase / snake_case / known tool token) do. Detection is query-side only."""
    q = (q or '').strip()
    toks = q.split()
    if not toks or len(toks) > 5:
        return False
    for idx, t in enumerate(toks):
        core = t.strip('«».,:;"\'()?!')
        if not core:
            continue
        low = core.lower()
        if low in ENTITY_TOOLS:        return True   # known product/project name
        if '_' in low:                 return True   # snake_case tool name
        if len(core) >= 4 and any(c.isupper() for c in core[1:]) and any(c.islower() for c in core):
            return True                              # CamelCase (NotebookLM, PyTorch)
        if core[:1].isupper() and (idx > 0 or len(toks) <= 2):
            if not (core.isupper() and len(core) <= 3):   # ignore bare topic-acronyms (AI, RAG, DAO)
                return True                          # proper noun (Feynman, Turing)
    return False


WIKILINK_RX = re.compile(r'\[\[([^\]\|#]+)')

def _links_in(path):
    """Outgoing wikilink TARGETS in a note (alias/heading stripped)."""
    try: t = Path(path).read_text(encoding='utf-8', errors='ignore')
    except Exception: return []
    return [m.strip() for m in WIKILINK_RX.findall(t)]


def main():
    args = sys.argv[1:]
    ask = '--ask' in args
    graph = '--graph' in args; ab = '--ab' in args
    flags = {'--ask', '--graph', '--ab'}
    query = ' '.join(a for a in args if a not in flags)
    if not query or not EMB.exists():
        print('need an embedding index (%s) and a query — run index_notes.py first' % EMB); return

    from sentence_transformers import SentenceTransformer, CrossEncoder
    dev = pick_device()
    meta = pickle.loads(META.read_bytes()); emb = np.load(EMB)
    enc = SentenceTransformer(E5_MODEL, device=dev)
    qv = enc.encode(['query: ' + query], normalize_embeddings=True, convert_to_numpy=True)[0].astype('float32')
    sims = emb @ qv

    # dense retrieve -> dedup by path (best chunk per file) -> top-K unique files
    order, seen = [], set()
    for i in np.argsort(-sims):
        m = meta[i]
        if m['path'] in seen: continue
        seen.add(m['path']); order.append(int(i))
        if len(order) >= TOPK_RETRIEVE: break

    # Graph expansion: follow 1-hop OUTGOING wikilinks from the top vector hits, pull in
    # those neighbour notes (resolved to already-indexed chunks), then let the reranker
    # decide if they're relevant. Bounded + opt-in (--graph) so base recall is untouched.
    # Serendipity that the reranker keeps honest.
    # CRASH-SAFETY: wrapped so ANY failure degrades to vector-only — worst case you get
    # the old result, never an empty/broken recall.
    base_order = list(order); added = []
    if (graph or ab) and order:
        try:
            by_base = {}                     # basename(no .md) -> meta indices (resolve link targets)
            for j, mm in enumerate(meta):
                by_base.setdefault(Path(mm['path']).stem.lower(), []).append(j)
            in_order = set(base_order)
            for i in base_order[:GHOPS]:
                for tgt in _links_in(meta[i]['path']):
                    idxs = by_base.get(tgt.lower())
                    if not idxs: continue
                    best = max(idxs, key=lambda j: sims[j])   # best chunk of neighbour for THIS query
                    if best in in_order: continue
                    in_order.add(best); added.append(best)
                    if len(added) >= GMAX: break
                if len(added) >= GMAX: break
        except Exception as e:
            added = []                       # graph failed -> fall back to vector only
            sys.stderr.write("graph-expansion fell back to vector (%s)\n" % e)
    graph_order = base_order + added; graph_set = set(added)

    # rerank on the matched chunk (precise)
    ce = CrossEncoder(RERANK_MODEL, device=dev)
    def rerank(cand):
        pairs = [(query, meta[i]['title'] + '. ' + meta[i]['snippet']) for i in cand]
        sc = ce.predict(pairs) if len(pairs) else []
        return sorted(zip(cand, sc), key=lambda x: -x[1])[:TOPN]

    # --ab: run BOTH paths in ONE process, diff, log. Answers "does graph REALLY help?"
    if ab:
        A = rerank(base_order); B = rerank(graph_order)
        a_paths = {meta[i]['path'] for i, _ in A}
        promoted = [(r, meta[i]['title']) for r, (i, _) in enumerate(B, 1)
                    if i in graph_set]                        # graph notes that reached top-N
        new_in_B = [(r, meta[i]['title']) for r, (i, _) in enumerate(B, 1)
                    if meta[i]['path'] not in a_paths]        # any note B surfaced that A missed
        out = [f'A/B RECALL: {query}',
               f'(Direct={len(base_order)} vector / Associative=+{len(added)} graph; '
               f'Associative promoted {len(new_in_B)} new notes into top-{TOPN}, {len(promoted)} of them via graph)', '',
               '--- DIRECT memory (vector) ---']
        for r, (i, sc) in enumerate(A, 1):
            out.append('%2d. [%6.2f] %s' % (r, float(sc), meta[i]['title']))
        out += ['', '--- ASSOCIATIVE memory (vector+graph) ---']
        for r, (i, sc) in enumerate(B, 1):
            mk = ('  <== +graph' if i in graph_set else
                  ('  <== NEW' if meta[i]['path'] not in a_paths else ''))
            out.append('%2d. [%6.2f] %s%s' % (r, float(sc), meta[i]['title'], mk))
        text = '\n'.join(out)
        try:
            con = sqlite3.connect(str(AB_DB), timeout=3.0)
            con.execute("""CREATE TABLE IF NOT EXISTS ab_recall(
                id INTEGER PRIMARY KEY AUTOINCREMENT, ts TEXT, query TEXT,
                cand_added INTEGER, new_in_top INTEGER, promoted_via_graph INTEGER,
                promoted_titles TEXT, source TEXT)""")
            con.execute("INSERT INTO ab_recall(ts,query,cand_added,new_in_top,promoted_via_graph,promoted_titles,source)"
                        " VALUES(?,?,?,?,?,?,?)",
                        (time.strftime('%Y-%m-%d %H:%M:%S'), query, len(added), len(new_in_B),
                         len(promoted), '; '.join(t for _, t in promoted)[:1000],
                         os.getenv('AB_SOURCE', 'work')))
            con.commit(); con.close()
        except Exception:
            pass
        jpath = os.getenv('BRAIN_AB_JSON')   # structured A/B for a dashboard
        if jpath:
            import json as _json
            def _ser(lst):
                return [{'rank': r, 'title': meta[i]['title'], 'score': round(float(sc), 2),
                         'snippet': meta[i].get('snippet', '')[:200],
                         'graph': i in graph_set,
                         'new': meta[i]['path'] not in a_paths}
                        for r, (i, sc) in enumerate(lst, 1)]
            try:
                Path(jpath).write_text(_json.dumps(
                    {'query': query, 'added': len(added), 'new_in_top': len(new_in_B),
                     'direct': _ser(A), 'associative': _ser(B)}, ensure_ascii=False), encoding='utf-8')
            except Exception:
                pass
        _out = Path(os.getenv('BRAIN_ANSWER_OUT', '_brain_answer.txt'))
        _out.write_text(text, encoding='utf-8'); print(text)
        return

    # GATE: wiki-graph helps THEME queries, hurts ENTITY/name queries -> skip graph for entities.
    gated = graph and looks_like_entity(query)
    if gated:
        sys.stderr.write("wiki-graph gated OFF (entity-like query) -> Direct only\n")
    order = graph_order if (graph and not gated) else base_order
    reranked = rerank(order)
    scope = 'all' + (' /graph+%d' % len(graph_set) if graph else '')
    lines = [f'QUERY: {query}', f'(scope={scope}; e5 {len(order)} files -> rerank; {len(meta)} chunks)', '']
    if ask:
        lines.append('=== CONTEXT BUNDLE (paste to your agent) ===\n')
        for i, sc in reranked[:8]:
            m = meta[i]
            hdr = "## %s  [%s] rr=%.2f%s" % (m['title'], m.get('date', ''), float(sc), ' +graph' if i in graph_set else '')
            lines += [hdr, m['snippet'], '']
    else:
        for rank, (i, sc) in enumerate(reranked, 1):
            m = meta[i]
            gmark = ' +graph' if i in graph_set else ''
            lines.append("%2d. [rr=%6.2f] [%s] %s%s" % (rank, float(sc), m.get('date', ''), m['title'], gmark))
            lines.append("     %s" % m['snippet'][:140])
    _out = Path(os.getenv('BRAIN_ANSWER_OUT', '_brain_answer.txt'))
    _out.write_text('\n'.join(lines), encoding='utf-8')
    print(f'{len(reranked)} hits (scope={scope}) -> {_out}')


if __name__ == '__main__':
    main()
