# -*- coding: utf-8 -*-
"""
Stop hook — PER-TURN SEMANTIC STATE LEDGER (Phase 1 of always-on agent memory).

After EVERY assistant turn, write a small DETERMINISTIC "what just happened" row to a
SQLite ledger (TurnState). 0 LLM tokens: everything is extracted by parsing the transcript
tail (user ask, assistant summary, files touched, tools used, commands, decision/TODO lines,
evidence pointer back to the raw transcript). The raw .jsonl stays the immutable source;
this is the cheap DERIVED working-state (distilled, not a replacement).

Written for Claude Code's Stop-hook protocol (JSON event on stdin with session_id,
transcript_path, cwd), but the pattern ports to any agent harness that exposes a
per-turn transcript.

Design principles:
- pure stdlib (sqlite3 + json + re), 0 deps, milliseconds.
- INCREMENTAL: per-session byte offset in %temp% -> reads only NEW lines each turn.
- NEVER breaks a session: ANY error -> exit 0, no output. Outputs nothing on success either
  (a Stop hook that prints structured JSON could alter control flow; we only WRITE to SQLite).
- LLM extraction is deliberately NOT here (cost gate). If the deterministic delta proves
  too thin, a cheap model extractor can be added in a later pass.

Config (env, optional):
  TURNSTATE_DB  path to the ledger db (default: ~/.claude/turnstate/turnstate.db)
"""
import sys, os, json, re, sqlite3, tempfile, time


def db_path():
    env = os.getenv("TURNSTATE_DB")
    if env:
        return env
    base = os.path.join(os.path.expanduser("~"), ".claude", "turnstate")
    try:
        os.makedirs(base, exist_ok=True)
        return os.path.join(base, "turnstate.db")
    except Exception:
        return os.path.join(tempfile.gettempdir(), "turnstate.db")

CLIP_ASK = 500
CLIP_SUMMARY = 1200
CLIP_CMD = 200
MAX_NEW_BYTES = 4 * 1024 * 1024   # don't read more than 4MB of new tail in one turn

# Lines that look like a decision / commitment / next step (English + Russian).
DECISION_RX = re.compile(
    r"(реш(?:ил|ено|аем)|дел(?:аем|ать)\b|не дел(?:аем|ать)|выбра(?:л|ли|ем)|вердикт|"
    r"\btodo\b|to-?do|next step|следующий шаг|\bвыбор\b|"
    r"decision|decided|we (?:will|should)|рекомендаци)",
    re.IGNORECASE)

FILE_TOOLS = {"Write", "Edit", "MultiEdit", "NotebookEdit"}


def text_of(ev):
    msg = ev.get("message") or {}
    c = msg.get("content")
    if isinstance(c, str):
        return c.strip()
    if isinstance(c, list):
        out = []
        for b in c:
            if isinstance(b, dict) and b.get("type") == "text":
                out.append(b.get("text") or "")
        return " ".join(p.strip() for p in out if p).strip()
    return ""


def is_real_user_text(t):
    if not t or len(t) < 3:
        return False
    head = t.lstrip()[:40].lower()
    if head.startswith("<") or "system-reminder" in head or "<command-" in head:
        return False
    if "caveat:" in head or "local-command" in head:
        return False
    return True


def tool_uses(ev):
    """Yield (name, input_dict) for tool_use blocks in an assistant event."""
    msg = ev.get("message") or {}
    c = msg.get("content")
    if isinstance(c, list):
        for b in c:
            if isinstance(b, dict) and b.get("type") == "tool_use":
                yield (b.get("name") or "", b.get("input") or {})


def offset_file(sid):
    sid = "".join(ch for ch in str(sid or "unknown") if ch.isalnum() or ch in "-_")[:80]
    return os.path.join(tempfile.gettempdir(), "claude-turnstate-%s.off" % (sid or "unknown"))


def read_offset(sid, fsize):
    try:
        with open(offset_file(sid), "r") as f:
            off = int(f.read().strip())
        if 0 <= off <= fsize:
            return off
    except Exception:
        pass
    return 0


def write_offset(sid, off):
    try:
        with open(offset_file(sid), "w") as f:
            f.write(str(off))
    except Exception:
        pass


def ensure_db(con):
    con.execute("""CREATE TABLE IF NOT EXISTS turns(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        session_id TEXT, ts TEXT, cwd TEXT, project TEXT,
        ask TEXT, summary TEXT,
        files TEXT, tools TEXT, commands TEXT, decisions TEXT,
        evidence TEXT, byte_start INTEGER, byte_end INTEGER
    )""")
    con.execute("CREATE INDEX IF NOT EXISTS ix_turns_sid ON turns(session_id)")
    con.commit()


def main():
    raw = sys.stdin.buffer.read()
    data = json.loads(raw.decode("utf-8", errors="replace"))

    sid = str(data.get("session_id") or "")
    tpath = data.get("transcript_path") or ""
    cwd = data.get("cwd") or ""
    if (not tpath or not os.path.isfile(tpath)) and sid:
        # fallback: some hook events may omit transcript_path.
        # locate the session's transcript by id under ~/.claude/projects/*/<sid>.jsonl
        import glob as _glob
        cands = _glob.glob(os.path.join(os.path.expanduser("~"), ".claude",
                                        "projects", "*", sid + ".jsonl"))
        if cands:
            tpath = max(cands, key=os.path.getmtime)
    if not tpath or not os.path.isfile(tpath):
        return

    fsize = os.path.getsize(tpath)
    start = read_offset(sid, fsize)
    if fsize - start > MAX_NEW_BYTES:        # huge gap -> skip body, just advance
        write_offset(sid, fsize)
        return
    if fsize <= start:                        # nothing new
        return

    with open(tpath, "r", encoding="utf-8", errors="ignore") as fh:
        fh.seek(start)
        chunk = fh.read()
    write_offset(sid, fsize)                   # advance regardless, never re-process

    ask = summary = ""
    files, tools, commands, decisions = [], [], [], []
    summary_parts = []
    for line in chunk.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            ev = json.loads(line)
        except Exception:
            continue
        t = ev.get("type")
        if t == "user":
            txt = text_of(ev)
            if is_real_user_text(txt):
                ask = txt           # last real user msg in this slice = the turn's ask
        elif t == "assistant":
            txt = text_of(ev)
            if txt:
                summary_parts.append(txt)
            for name, inp in tool_uses(ev):
                if name:
                    tools.append(name)
                if name in FILE_TOOLS:
                    fp = inp.get("file_path") or inp.get("notebook_path")
                    if fp:
                        files.append(fp)
                if name == "Bash":
                    cmd = (inp.get("command") or "").strip()
                    if cmd:
                        commands.append(cmd[:CLIP_CMD])

    summary = "\n".join(summary_parts).strip()
    for ln in summary.splitlines():
        ln = ln.strip(" -*#>").strip()
        if 8 <= len(ln) <= 180 and DECISION_RX.search(ln):
            decisions.append(ln)

    # nothing meaningful happened (e.g. pure tool-result turn) -> skip writing noise
    if not (ask or summary or files or commands):
        return

    def uniq(seq):
        seen, out = set(), []
        for x in seq:
            if x not in seen:
                seen.add(x); out.append(x)
        return out

    row = (
        sid,
        time.strftime("%Y-%m-%d %H:%M:%S"),
        cwd,
        os.path.basename(cwd.rstrip("\\/")) if cwd else "",
        ask[:CLIP_ASK],
        summary[:CLIP_SUMMARY],
        json.dumps(uniq(files), ensure_ascii=False),
        json.dumps(uniq(tools), ensure_ascii=False),
        json.dumps(commands[:20], ensure_ascii=False),
        json.dumps(uniq(decisions)[:10], ensure_ascii=False),
        tpath,
        start,
        fsize,
    )
    con = sqlite3.connect(db_path(), timeout=3.0)
    try:
        ensure_db(con)
        con.execute(
            "INSERT INTO turns(session_id,ts,cwd,project,ask,summary,files,tools,"
            "commands,decisions,evidence,byte_start,byte_end) "
            "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)", row)
        con.commit()
    finally:
        con.close()


if __name__ == "__main__":
    try:
        main()
    except Exception:
        pass   # never break a session
    sys.exit(0)
