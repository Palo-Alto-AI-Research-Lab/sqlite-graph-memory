# -*- coding: utf-8 -*-
"""
Viewer for the per-turn semantic-state ledger (TurnState).
Read-only. 0 tokens.

Usage:
  python turnstate_show.py                 # last 15 turns (all sessions)
  python turnstate_show.py --session <id>  # one session
  python turnstate_show.py --n 40          # last N
  python turnstate_show.py --stats         # totals

Config (env, optional):
  TURNSTATE_DB  path to the ledger db (default: ~/.claude/turnstate/turnstate.db)
"""
import sqlite3, json, os, sys, argparse

DB = os.getenv("TURNSTATE_DB") or os.path.join(
    os.path.expanduser("~"), ".claude", "turnstate", "turnstate.db")

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass


def jl(s):
    try:
        return json.loads(s or "[]")
    except Exception:
        return []


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--session")
    ap.add_argument("--n", type=int, default=15)
    ap.add_argument("--stats", action="store_true")
    a = ap.parse_args()

    if not os.path.isfile(DB):
        print("(no ledger yet:", DB, ")"); return
    c = sqlite3.connect(DB)

    if a.stats:
        tot = c.execute("select count(*) from turns").fetchone()[0]
        sess = c.execute("select count(distinct session_id) from turns").fetchone()[0]
        files = c.execute("select count(*) from turns where files != '[]'").fetchone()[0]
        print("ledger:", DB)
        print("turns: %d | sessions: %d | turns-that-touched-files: %d" % (tot, sess, files))
        for sid, n, last in c.execute(
                "select session_id,count(*),max(ts) from turns group by session_id "
                "order by max(ts) desc limit 12"):
            print("  %-38s %4d turns  last %s" % (sid[:38], n, last))
        return

    q = ("select ts,project,ask,summary,files,tools,commands,decisions,session_id "
         "from turns ")
    args = ()
    if a.session:
        q += "where session_id=? "; args = (a.session,)
    q += "order by id desc limit ?"; args = args + (a.n,)

    rows = c.execute(q, args).fetchall()
    for ts, project, ask, summary, files, tools, commands, decisions, sid in reversed(rows):
        print("=" * 78)
        print("🕒 %s   [%s]   sid %s" % (ts, project, sid[:12]))
        if ask:
            print("❓ ask: %s" % ask.replace("\n", " ")[:160])
        f, t, cm, d = jl(files), jl(tools), jl(commands), jl(decisions)
        if f:
            print("📝 files: %s" % ", ".join(os.path.basename(x) for x in f[:6]))
        if t:
            print("🔧 tools: %s" % ", ".join(t[:12]))
        if cm:
            print("⌨  cmds: %d (%s ...)" % (len(cm), (cm[0][:60] if cm else "")))
        if d:
            print("✅ decisions:")
            for x in d[:6]:
                print("     - %s" % x)
        if summary:
            print("💬 summary: %s" % summary.replace("\n", " ")[:200])


if __name__ == "__main__":
    main()
