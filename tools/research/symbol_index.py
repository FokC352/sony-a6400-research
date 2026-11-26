#!/usr/bin/env python3
"""Tier-1 SQLite symbolic index + Tier-2 persistent WSL2 Headless Ghidra daemon.

Tier 1 stores ELF symbols, direct callgraph edges, IPC constant call sites,
.rodata strings, and SHA-256-keyed C decompilation cache in SQLite WAL mode for
sub-millisecond lookup and trace symbolization without re-scanning ELFs.

Tier 2 keeps one JVM + DecompInterface process resident on WSL2 loopback TCP so
on-demand Ghidra decompilation of arbitrary functions finishes in <1-2 seconds
without JVM startup or full-binary auto-analysis.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import secrets
import shutil
import socket
import sqlite3
import subprocess
import sys
import time
from typing import Any

from project_paths import PROJECT_ROOT
from tools.research.elf32_lite import (
    Elf32Image,
    build_callgraph_lite,
    extract_ipc_sites_lite,
    make_enclosing,
)

DEFAULT_WSL_INDEX_DIR = Path("/home/administrator/a6400-ghidra-index")
DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 47640
DEFAULT_INCLUDE_NAMES = ("im.elf", "libObj.so", "viewUnified2.so", "libtestcmd.so")


def default_index_dir() -> Path:
    env = os.environ.get("A6400_SYMBOL_INDEX_DIR")
    if env:
        return Path(env)
    if os.name == "posix":
        return DEFAULT_WSL_INDEX_DIR
    return PROJECT_ROOT / "artifacts" / "cache" / "symbol-index"


def default_db_path() -> Path:
    env = os.environ.get("A6400_SYMBOL_INDEX_DB")
    if env:
        return Path(env)
    return default_index_dir() / "symbol_index.sqlite3"


def default_state_path() -> Path:
    return default_index_dir() / "daemon.json"


def wsl_path(path: Path) -> str:
    p = Path(path).resolve()
    if os.name == "posix":
        return p.as_posix()
    drive = p.drive
    if not drive or len(drive) != 2:
        raise ValueError(f"Expected Windows drive path for WSL translation: {p}")
    return "/mnt/" + drive[0].lower() + p.as_posix()[2:]


def detect_ghidra_headless() -> Path:
    rea_root = Path(os.environ.get("A6400_REA_TOOLS", "/home/administrator/a6400-rea-tools"))
    for ver in ("ghidra_12.2_PUBLIC", "ghidra_12.1.4_PUBLIC"):
        cand = rea_root / ver / "support" / "analyzeHeadless"
        if cand.is_file():
            return cand
    found = sorted(rea_root.glob("ghidra_*/support/analyzeHeadless"))
    if found:
        return found[-1]
    raise FileNotFoundError(f"No analyzeHeadless installation found under {rea_root}")


def detect_java_home() -> str:
    for cand in (
        os.environ.get("JAVA_HOME", ""),
        "/usr/lib/jvm/java-21-openjdk-amd64",
        "/usr/lib/jvm/default-java",
    ):
        if cand and Path(cand).is_dir():
            return cand
    java_bin = shutil.which("java")
    if java_bin:
        return str(Path(java_bin).resolve().parents[1])
    raise FileNotFoundError("Unable to locate JDK installation for Ghidra")


# ---------------------------------------------------------------------------
# Tier-1 SQLite schema & operations
# ---------------------------------------------------------------------------

_SCHEMA_SQL = """
PRAGMA journal_mode=WAL;
PRAGMA synchronous=NORMAL;

CREATE TABLE IF NOT EXISTS binaries (
    binary_id   INTEGER PRIMARY KEY AUTOINCREMENT,
    body        TEXT NOT NULL,
    rel_path    TEXT NOT NULL,
    filename    TEXT NOT NULL,
    sha256      TEXT NOT NULL UNIQUE,
    e_type      INTEGER NOT NULL,
    base_va     INTEGER NOT NULL,
    indexed_utc TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS symbols (
    binary_id   INTEGER NOT NULL,
    address     INTEGER NOT NULL,
    thumb_bit   INTEGER NOT NULL,
    size        INTEGER NOT NULL,
    type        TEXT NOT NULL,
    source      TEXT NOT NULL,
    name        TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_symbols_name ON symbols(name);
CREATE INDEX IF NOT EXISTS idx_symbols_bin_addr ON symbols(binary_id, address);

CREATE TABLE IF NOT EXISTS edges (
    binary_id   INTEGER NOT NULL,
    caller_va   INTEGER NOT NULL,
    caller_name TEXT NOT NULL,
    callee_va   INTEGER NOT NULL,
    callee_name TEXT NOT NULL,
    kind        TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_edges_caller ON edges(binary_id, caller_name);
CREATE INDEX IF NOT EXISTS idx_edges_callee ON edges(binary_id, callee_name);

CREATE TABLE IF NOT EXISTS ipc_sites (
    binary_id    INTEGER NOT NULL,
    site_va      INTEGER NOT NULL,
    caller_name  TEXT NOT NULL,
    callee_name  TEXT NOT NULL,
    queue_or_cmd INTEGER
);
CREATE INDEX IF NOT EXISTS idx_ipc_caller ON ipc_sites(caller_name);
CREATE INDEX IF NOT EXISTS idx_ipc_queue ON ipc_sites(queue_or_cmd);

CREATE TABLE IF NOT EXISTS strings (
    binary_id   INTEGER NOT NULL,
    va          INTEGER NOT NULL,
    value       TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_strings_val ON strings(value);

CREATE TABLE IF NOT EXISTS decompile_cache (
    sha256          TEXT NOT NULL,
    func_va         INTEGER NOT NULL,
    func_name       TEXT NOT NULL,
    ghidra_addr     TEXT NOT NULL,
    c_code          TEXT NOT NULL,
    ghidra_version  TEXT NOT NULL,
    created_utc     TEXT NOT NULL,
    PRIMARY KEY (sha256, func_va)
);
"""


def open_db(db_path: Path | None = None) -> sqlite3.Connection:
    target = Path(db_path or default_db_path())
    target.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(target), timeout=30.0)
    conn.row_factory = sqlite3.Row
    conn.executescript(_SCHEMA_SQL)
    return conn


def index_elf(
    conn: sqlite3.Connection,
    elf_path: Path,
    *,
    body: str = "ILCE-6400",
    rel_root: Path | None = None,
    include_edges: bool = True,
) -> dict[str, Any]:
    """Index symbols, callgraph edges, IPC sites, and .rodata strings for one ELF."""
    img = Elf32Image.from_file(elf_path)
    existing = conn.execute(
        "SELECT binary_id, sha256 FROM binaries WHERE sha256 = ?", (img.sha256,)
    ).fetchone()
    if existing is not None:
        return {"file": elf_path.name, "sha256": img.sha256, "status": "already_indexed"}

    try:
        rel_path = elf_path.resolve().relative_to(rel_root.resolve()).as_posix() if rel_root else elf_path.name
    except ValueError:
        rel_path = elf_path.name

    now = datetime.now(timezone.utc).isoformat()
    base_va = img.base_vaddr()
    cur = conn.execute(
        "INSERT INTO binaries(body, rel_path, filename, sha256, e_type, base_va, indexed_utc) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        (body, rel_path, elf_path.name, img.sha256, img.e_type, base_va, now),
    )
    bid = int(cur.lastrowid)

    syms = img.symbols()
    sym_rows = [
        (bid, s.address & ~1, s.address & 1, s.size, s.type, s.source, s.name)
        for s in syms
        if s.address
    ]
    conn.executemany(
        "INSERT INTO symbols(binary_id, address, thumb_bit, size, type, source, name) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        sym_rows,
    )

    edge_count = 0
    ipc_count = 0
    if include_edges:
        try:
            cg = build_callgraph_lite(img)
            edge_rows = [
                (bid, c_va, c_name, t_va & ~1, t_name, kind)
                for c_va, c_name, t_va, t_name, kind in cg["edges"]
            ]
            conn.executemany(
                "INSERT INTO edges(binary_id, caller_va, caller_name, callee_va, callee_name, kind) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                edge_rows,
            )
            edge_count = len(edge_rows)
        except ValueError:
            pass

        try:
            ipc_rows = [
                (bid, row["site"], row["caller"], row["callee"], row["queue_or_cmd"])
                for row in extract_ipc_sites_lite(img)
            ]
            conn.executemany(
                "INSERT INTO ipc_sites(binary_id, site_va, caller_name, callee_name, queue_or_cmd) "
                "VALUES (?, ?, ?, ?, ?)",
                ipc_rows,
            )
            ipc_count = len(ipc_rows)
        except ValueError:
            pass

    str_rows = [(bid, va, val) for va, val in img.rodata_strings(min_len=5)]
    conn.executemany(
        "INSERT INTO strings(binary_id, va, value) VALUES (?, ?, ?)", str_rows
    )
    conn.commit()
    return {
        "file": elf_path.name,
        "sha256": img.sha256,
        "status": "indexed",
        "symbols": len(sym_rows),
        "edges": edge_count,
        "ipc_sites": ipc_count,
        "strings": len(str_rows),
    }


def build_index_from_root(
    conn: sqlite3.Connection,
    root: Path,
    *,
    body: str = "ILCE-6400",
    include: tuple[str, ...] = DEFAULT_INCLUDE_NAMES,
) -> list[dict[str, Any]]:
    """Index matching ELF files under ``root`` into Tier-1 SQLite."""
    include_set = set(include)
    results: list[dict[str, Any]] = []
    seen_paths: set[Path] = set()
    for path in sorted(root.rglob("*")):
        if not path.is_file():
            continue
        if include_set and path.name not in include_set:
            continue
        if "nflasha1_unpacked" in path.parts:
            continue
        resolved = path.resolve()
        if resolved in seen_paths:
            continue
        seen_paths.add(resolved)
        results.append(index_elf(conn, resolved, body=body, rel_root=root))
    return results


def lookup_symbol_or_va(
    conn: sqlite3.Connection,
    query: str,
    *,
    body: str | None = None,
    binary: str | None = None,
    limit: int = 25,
) -> dict[str, Any]:
    """Query symbols by exact name, substring, or hex VA, plus .rodata strings."""
    filters = []
    params: list[Any] = []
    if body:
        filters.append("b.body = ?")
        params.append(body)
    if binary:
        filters.append("b.filename = ?")
        params.append(binary)
    where_prefix = (" AND ".join(filters) + " AND ") if filters else ""

    va_val: int | None = None
    try:
        va_val = int(query, 16) if query.startswith(("0x", "0X")) else int(query, 0)
    except ValueError:
        va_val = None

    if va_val is not None:
        even = va_val & ~1
        rows = conn.execute(
            f"SELECT b.body, b.filename, b.sha256, s.address, s.thumb_bit, s.size, s.type, s.name "
            f"FROM symbols s JOIN binaries b ON s.binary_id = b.binary_id "
            f"WHERE {where_prefix} s.address <= ? AND (s.size = 0 AND s.address = ? OR s.address + s.size > ?) "
            f"ORDER BY (s.address = ?) DESC, s.address DESC LIMIT ?",
            [*params, even, even, even, even, limit],
        ).fetchall()
        str_hits = []
    else:
        rows = conn.execute(
            f"SELECT b.body, b.filename, b.sha256, s.address, s.thumb_bit, s.size, s.type, s.name "
            f"FROM symbols s JOIN binaries b ON s.binary_id = b.binary_id "
            f"WHERE {where_prefix} (s.name = ? OR s.name LIKE ?) "
            f"ORDER BY (s.name = ?) DESC, length(s.name) ASC LIMIT ?",
            [*params, query, f"%{query}%", query, limit],
        ).fetchall()
        str_hits = [
            dict(r)
            for r in conn.execute(
                f"SELECT b.body, b.filename, st.va, st.value "
                f"FROM strings st JOIN binaries b ON st.binary_id = b.binary_id "
                f"WHERE {where_prefix} st.value LIKE ? LIMIT ?",
                [*params, f"%{query}%", limit],
            ).fetchall()
        ]

    return {
        "query": query,
        "symbols": [
            {
                "body": r["body"],
                "binary": r["filename"],
                "sha256": r["sha256"],
                "address": f"0x{(r['address'] | r['thumb_bit']):08x}",
                "even_va": r["address"],
                "thumb": bool(r["thumb_bit"]),
                "size": r["size"],
                "type": r["type"],
                "name": r["name"],
            }
            for r in rows
        ],
        "strings": [
            {
                "body": s["body"],
                "binary": s["filename"],
                "va": f"0x{s['va']:08x}",
                "value": s["value"],
            }
            for s in str_hits
        ],
    }


def query_callers_callees(
    conn: sqlite3.Connection,
    direction: str,
    symbol_name: str,
    *,
    binary: str | None = None,
    limit: int = 50,
) -> list[dict[str, Any]]:
    col = "e.callee_name" if direction == "callers" else "e.caller_name"
    extra = " AND b.filename = ?" if binary else ""
    args: list[Any] = [symbol_name]
    if binary:
        args.append(binary)
    args.append(limit)
    rows = conn.execute(
        f"SELECT b.body, b.filename, e.caller_va, e.caller_name, e.callee_va, e.callee_name, e.kind "
        f"FROM edges e JOIN binaries b ON e.binary_id = b.binary_id "
        f"WHERE {col} = ?{extra} LIMIT ?",
        args,
    ).fetchall()
    return [
        {
            "body": r["body"],
            "binary": r["filename"],
            "caller_va": f"0x{r['caller_va']:08x}",
            "caller_name": r["caller_name"],
            "callee_va": f"0x{r['callee_va']:08x}",
            "callee_name": r["callee_name"],
            "kind": r["kind"],
        }
        for r in rows
    ]


def symbolize_offset(
    conn: sqlite3.Connection, binary: str, offset_va: int, *, body: str | None = None
) -> str:
    """Resolve ``(binary, relative_va)`` into ``binary!symbol+0xoff`` or ``sub_XXXXXXXX``."""
    even = offset_va & ~1
    filters = ["b.filename = ?"]
    params: list[Any] = [binary]
    if body:
        filters.append("b.body = ?")
        params.append(body)
    where = " AND ".join(filters)
    row = conn.execute(
        f"SELECT s.name, s.address, s.size FROM symbols s "
        f"JOIN binaries b ON s.binary_id = b.binary_id "
        f"WHERE {where} AND s.type IN ('T', 't') AND s.address <= ? "
        f"AND ((s.size > 0 AND s.address + s.size > ?) OR (s.size = 0 AND s.address = ?)) "
        f"ORDER BY s.address DESC LIMIT 1",
        [*params, even, even, even],
    ).fetchone()
    if row is None:
        return f"{binary}!sub_{even:08X}"
    delta = even - row["address"]
    return f"{binary}!{row['name']}+0x{delta:x}" if delta else f"{binary}!{row['name']}"


# ---------------------------------------------------------------------------
# Tier-2 Persistent WSL2 Headless Ghidra Daemon
# ---------------------------------------------------------------------------

def _read_state(state_path: Path | None = None) -> dict[str, Any] | None:
    sp = Path(state_path or default_state_path())
    if not sp.is_file():
        return None
    try:
        return json.loads(sp.read_text(encoding="utf-8"))
    except Exception:
        return None


def _rpc_call(host: str, port: int, token: str, payload: dict[str, Any], timeout: float = 120.0) -> dict[str, Any]:
    req = dict(payload)
    if token:
        req["token"] = token
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.settimeout(timeout)
    try:
        sock.connect((host, port))
        sock.sendall((json.dumps(req) + "\n").encode("utf-8"))
        chunks: list[bytes] = []
        while True:
            data = sock.recv(65536)
            if not data:
                break
            chunks.append(data)
            if b"\n" in data:
                break
        text = b"".join(chunks).decode("utf-8", errors="replace").strip()
        if not text:
            raise RuntimeError("Empty RPC reply from PersistentSymbolDaemon")
        return json.loads(text.splitlines()[0])
    finally:
        sock.close()


def daemon_status(state_path: Path | None = None) -> dict[str, Any]:
    if os.name != "posix":
        res = subprocess.run(
            ["wsl.exe", "--exec", "python3", "-m", "tools.research.symbol_index", "daemon", "status"],
            capture_output=True,
            text=True,
            timeout=15,
        )
        if res.returncode != 0:
            return {"running": False, "error": res.stderr.strip() or res.stdout.strip()}
        return json.loads(res.stdout)

    state = _read_state(state_path)
    if not state:
        return {"running": False, "reason": "no daemon.json"}
    try:
        reply = _rpc_call(
            DEFAULT_HOST,
            int(state["port"]),
            state.get("token", ""),
            {"op": "ping"},
            timeout=3.0,
        )
        if reply.get("ok"):
            return {
                "running": True,
                "pid": reply.get("pid", state.get("pid")),
                "port": state["port"],
                "ghidra_version": reply.get("ghidra_version"),
                "loaded_programs": reply.get("loaded_programs", 0),
                "started_utc": state.get("started_utc"),
            }
    except OSError:
        pass
    return {"running": False, "stale_state": state}


def _systemd_user_available() -> bool:
    if shutil.which("systemctl") is None or shutil.which("systemd-run") is None:
        return False
    res = subprocess.run(
        ["systemctl", "--user", "is-system-running"],
        capture_output=True,
        text=True,
        timeout=5,
    )
    return res.stdout.strip() in ("running", "degraded")


def daemon_start(
    *,
    port: int = DEFAULT_PORT,
    index_dir: Path | None = None,
    timeout: float = 90.0,
) -> dict[str, Any]:
    if os.name != "posix":
        res = subprocess.run(
            ["wsl.exe", "--exec", "python3", "-m", "tools.research.symbol_index", "daemon", "start", "--port", str(port)],
            capture_output=True,
            text=True,
            timeout=int(timeout) + 15,
        )
        if res.returncode != 0:
            raise RuntimeError(res.stderr.strip() or res.stdout.strip())
        return json.loads(res.stdout)

    base = Path(index_dir or default_index_dir())
    base.mkdir(parents=True, exist_ok=True)
    state_path = base / "daemon.json"
    cur = daemon_status(state_path)
    if cur.get("running"):
        return cur

    analyze = detect_ghidra_headless()
    java_home = detect_java_home()
    proj_dir = base / "ghidra-proj"
    proj_dir.mkdir(parents=True, exist_ok=True)
    scripts_dir = PROJECT_ROOT / "tools" / "research" / "ghidra"
    log_path = base / "daemon.log"
    token = secrets.token_hex(16)

    if state_path.exists():
        state_path.unlink()

    env_pairs = {
        "JAVA_HOME": java_home,
        "A6400_GHIDRA_PORT": str(port),
        "A6400_GHIDRA_TOKEN": token,
        "A6400_GHIDRA_STATE": str(state_path),
    }
    cmd = [
        str(analyze),
        str(proj_dir),
        "a6400_symbols",
        "-scriptPath",
        str(scripts_dir),
        "-preScript",
        "PersistentSymbolDaemon.java",
        "-noanalysis",
    ]

    proc: subprocess.Popen | None = None
    if _systemd_user_available():
        subprocess.run(
            ["systemctl", "--user", "reset-failed", "a6400-ghidra-daemon"],
            capture_output=True,
            timeout=5,
        )
        sd_cmd = [
            "systemd-run",
            "--user",
            "--unit=a6400-ghidra-daemon",
            "--collect",
            "--working-directory",
            str(PROJECT_ROOT),
            "--property=StandardOutput=append:" + str(log_path),
            "--property=StandardError=append:" + str(log_path),
        ]
        for k, v in env_pairs.items():
            sd_cmd.extend(["--setenv", f"{k}={v}"])
        sd_cmd.extend(cmd)
        subprocess.run(sd_cmd, check=True, capture_output=True, text=True, timeout=15)
    else:
        env = dict(os.environ, **env_pairs)
        with log_path.open("ab") as log_f:
            proc = subprocess.Popen(
                cmd,
                stdin=subprocess.DEVNULL,
                stdout=log_f,
                stderr=subprocess.STDOUT,
                env=env,
                start_new_session=True,
            )

    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if proc is not None and proc.poll() is not None:
            tail = log_path.read_text(encoding="utf-8", errors="replace")[-1200:]
            raise RuntimeError(f"PersistentSymbolDaemon exited ({proc.returncode}): {tail}")
        st = daemon_status(state_path)
        if st.get("running"):
            return st
        time.sleep(0.25)
    if proc is not None:
        proc.terminate()
    raise TimeoutError(f"Timed out waiting for PersistentSymbolDaemon ({log_path})")


def daemon_stop(state_path: Path | None = None) -> dict[str, Any]:
    if os.name != "posix":
        res = subprocess.run(
            ["wsl.exe", "--exec", "python3", "-m", "tools.research.symbol_index", "daemon", "stop"],
            capture_output=True,
            text=True,
            timeout=15,
        )
        return json.loads(res.stdout) if res.returncode == 0 else {"stopped": False}

    sp = Path(state_path or default_state_path())
    state = _read_state(sp)
    was_running = False
    if state:
        was_running = True
        try:
            _rpc_call(DEFAULT_HOST, int(state["port"]), state.get("token", ""), {"op": "shutdown"}, timeout=5.0)
        except OSError:
            pass
    if _systemd_user_available():
        subprocess.run(
            ["systemctl", "--user", "stop", "a6400-ghidra-daemon"],
            capture_output=True,
            timeout=10,
        )
    if sp.exists():
        sp.unlink()
    return {"stopped": True, "was_running": was_running}


def decompile_function(
    elf_path: Path,
    va_or_symbol: str,
    *,
    db_path: Path | None = None,
    timeout_sec: int = 60,
) -> dict[str, Any]:
    """Decompile a function via SQLite cache -> persistent WSL2 Ghidra daemon."""
    if os.name != "posix":
        res = subprocess.run(
            [
                "wsl.exe",
                "--exec",
                "python3",
                "-m",
                "tools.research.symbol_index",
                "decompile",
                "--elf",
                wsl_path(elf_path),
                "--target",
                va_or_symbol,
                "--timeout",
                str(timeout_sec),
                "--json",
            ],
            capture_output=True,
            text=True,
            timeout=max(90, timeout_sec + 60),
        )
        if res.returncode != 0:
            raise RuntimeError(res.stderr.strip() or res.stdout.strip())
        return json.loads(res.stdout)

    img = Elf32Image.from_file(elf_path)
    sha = img.sha256
    base_va = img.base_vaddr()

    va_val: int | None = None
    try:
        va_val = int(va_or_symbol, 16) if va_or_symbol.startswith(("0x", "0X")) else int(va_or_symbol, 0)
    except ValueError:
        for sym in img.symbols():
            if sym.name == va_or_symbol and sym.address:
                va_val = sym.address
                break
        if va_val is None:
            raise ValueError(f"Symbol {va_or_symbol!r} not found in {elf_path}")

    even_va = va_val & ~1
    thumb_hint = bool(va_val & 1)
    if not thumb_hint:
        for sym in img.symbols():
            if (sym.address & ~1) == even_va and (sym.address & 1):
                thumb_hint = True
                break

    conn = open_db(db_path)
    try:
        cached = conn.execute(
            "SELECT func_name, ghidra_addr, c_code, ghidra_version, created_utc "
            "FROM decompile_cache WHERE sha256 = ? AND func_va = ?",
            (sha, even_va),
        ).fetchone()
        if cached is not None:
            return {
                "ok": True,
                "source": "sqlite_cache",
                "sha256": sha,
                "va": f"0x{va_val:08x}",
                "ghidra_addr": cached["ghidra_addr"],
                "func_name": cached["func_name"],
                "c_code": cached["c_code"],
                "ghidra_version": cached["ghidra_version"],
            }

        st = daemon_start()
        state = _read_state()
        assert state is not None
        reply = _rpc_call(
            DEFAULT_HOST,
            int(st["port"]),
            state.get("token", ""),
            {
                "op": "decompile",
                "elf_path": str(elf_path.resolve()),
                "sha256": sha,
                "va": va_val,
                "elf_base": base_va,
                "thumb": thumb_hint,
                "timeout_sec": timeout_sec,
            },
            timeout=float(timeout_sec + 30),
        )
        if not reply.get("ok"):
            raise RuntimeError(reply.get("error", "Unknown daemon error"))

        conn.execute(
            "INSERT OR REPLACE INTO decompile_cache"
            "(sha256, func_va, func_name, ghidra_addr, c_code, ghidra_version, created_utc) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                sha,
                even_va,
                reply["func_name"],
                reply["ghidra_addr"],
                reply["c_code"],
                reply.get("ghidra_version", ""),
                datetime.now(timezone.utc).isoformat(),
            ),
        )
        conn.commit()
        reply["source"] = "ghidra_daemon"
        reply["va"] = f"0x{va_val:08x}"
        return reply
    finally:
        conn.close()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, default=None, help="Override SQLite index path")
    sub = parser.add_subparsers(dest="cmd", required=True)

    b = sub.add_parser("build", help="Index ELF binaries from an unpacked firmware tree")
    b.add_argument("--root", type=Path, required=True)
    b.add_argument("--body", default="ILCE-6400")
    b.add_argument("--include", default=",".join(DEFAULT_INCLUDE_NAMES))

    lk = sub.add_parser("lookup", help="Look up symbol, VA, or .rodata string")
    lk.add_argument("query")
    lk.add_argument("--body", default=None)
    lk.add_argument("--binary", default=None)
    lk.add_argument("--limit", type=int, default=20)

    for cname in ("callers", "callees"):
        cp = sub.add_parser(cname, help=f"List {cname} of a symbol")
        cp.add_argument("symbol")
        cp.add_argument("--binary", default=None)
        cp.add_argument("--limit", type=int, default=50)

    sy = sub.add_parser("symbolize", help="Resolve binary relative VA into symbol+offset")
    sy.add_argument("--binary", required=True)
    sy.add_argument("--va", type=lambda v: int(v, 0), required=True)
    sy.add_argument("--body", default=None)

    dm = sub.add_parser("daemon", help="Manage persistent WSL2 Headless Ghidra daemon")
    dm.add_argument("action", choices=("start", "status", "stop", "ensure"))
    dm.add_argument("--port", type=int, default=DEFAULT_PORT)

    dc = sub.add_parser("decompile", help="Decompile function via cache or persistent Ghidra")
    dc.add_argument("--elf", type=Path, required=True)
    dc.add_argument("--target", required=True, help="Symbol name or VA (hex)")
    dc.add_argument("--timeout", type=int, default=60)
    dc.add_argument("--json", action="store_true")

    args = parser.parse_args(argv)
    if args.cmd == "build":
        inc = tuple(x.strip() for x in args.include.split(",") if x.strip())
        conn = open_db(args.db)
        try:
            res = build_index_from_root(conn, args.root, body=args.body, include=inc)
        finally:
            conn.close()
        print(json.dumps(res, indent=2))
        return 0

    if args.cmd == "lookup":
        conn = open_db(args.db)
        try:
            out = lookup_symbol_or_va(conn, args.query, body=args.body, binary=args.binary, limit=args.limit)
        finally:
            conn.close()
        print(json.dumps(out, indent=2))
        return 0

    if args.cmd in ("callers", "callees"):
        conn = open_db(args.db)
        try:
            out = query_callers_callees(conn, args.cmd, args.symbol, binary=args.binary, limit=args.limit)
        finally:
            conn.close()
        print(json.dumps(out, indent=2))
        return 0

    if args.cmd == "symbolize":
        conn = open_db(args.db)
        try:
            print(symbolize_offset(conn, args.binary, args.va, body=args.body))
        finally:
            conn.close()
        return 0

    if args.cmd == "daemon":
        if args.action == "status":
            print(json.dumps(daemon_status(), indent=2))
        elif args.action in ("start", "ensure"):
            print(json.dumps(daemon_start(port=args.port), indent=2))
        elif args.action == "stop":
            print(json.dumps(daemon_stop(), indent=2))
        return 0

    if args.cmd == "decompile":
        out = decompile_function(args.elf, args.target, db_path=args.db, timeout_sec=args.timeout)
        if args.json:
            print(json.dumps(out, indent=2))
        else:
            print(f"// {out['func_name']} @ {out['ghidra_addr']} ({out['source']})\n{out['c_code']}")
        return 0

    return 2


if __name__ == "__main__":
    raise SystemExit(main())
