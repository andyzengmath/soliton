#!/usr/bin/env python3
"""Soliton MCP shim — exposes the 7 expected `graph-cli` subcommands by speaking
JSON-RPC over stdio to `code-review-graph serve`.

Closes POST_V2_FOLLOWUPS §B2 (the auth-free / sibling-repo-decoupled half of the
graph-signals story). Today, partial-mode `code-review-graph` covers only `info`
and `dependency-breaks` via CLI subcommands; the other 5 Soliton queries
(`blast-radius`, `taint-paths`, `co-change`, `feature-partition`,
`review-bundle`) are MCP-only on its 28-tool surface. This shim forks
`code-review-graph serve` once, maintains a long-lived JSON-RPC stdio connection,
and routes each Soliton subcommand to the right MCP tool with output translation
into Soliton's expected JSON shape.

Status: STARTER — info + blast-radius end-to-end, 5 others stubbed with TODO
markers and the exact MCP tool name they need to call. Smoke test at
`tests/test_mcp_shim.py`. Latency characterisation + the full 5-query rollout
will land in a follow-up PR.

Usage (drop-in replacement for the would-be `graph-cli` binary in
`skills/pr-review/graph-signals.md` Mode B):

    python bench/graph/mcp_shim.py info --graph .code-review-graph/graph.db
    python bench/graph/mcp_shim.py blast-radius src/foo.py:bar --depth 2
    python bench/graph/mcp_shim.py dependency-breaks --base HEAD~1

Subprocess lifecycle: a single `code-review-graph serve` child is forked at
shim startup, JSON-RPC `initialize` handshake completes, then each subcommand
is one `tools/call` round-trip. Caller can issue many subcommands per shim
process to amortise the ~1s server startup cost.

Wire format reference:
- MCP spec: https://spec.modelcontextprotocol.io
- code-review-graph tool catalog: `code-review-graph serve --help` (28 tools)
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import threading
from pathlib import Path
from typing import Any


JSONRPC = "2.0"
MCP_PROTOCOL_VERSION = "2024-11-05"


class McpServerError(RuntimeError):
    """Raised when the MCP server returns an error or fails to start."""


class CrgMcpClient:
    """Stdio JSON-RPC client for `code-review-graph serve`. Maintains a long-lived
    subprocess and demuxes responses by id. Thread-safe for the read side; callers
    should serialize writes.
    """

    def __init__(self, repo: Path | None = None, serve_cmd: str = "code-review-graph"):
        self._cmd = [serve_cmd, "serve"]
        if repo is not None:
            self._cmd += ["--repo", str(repo)]
        self._proc: subprocess.Popen | None = None
        self._next_id = 1
        self._pending: dict[int, dict] = {}
        self._lock = threading.Lock()
        self._stderr_lines: list[str] = []
        self._reader_thread: threading.Thread | None = None
        self._stderr_thread: threading.Thread | None = None

    def start(self) -> None:
        """Fork the server and run the MCP `initialize` handshake."""
        if self._proc is not None:
            return
        self._proc = subprocess.Popen(
            self._cmd,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            bufsize=1,
            env=os.environ.copy(),
        )
        self._reader_thread = threading.Thread(target=self._read_stdout_loop, daemon=True)
        self._reader_thread.start()
        self._stderr_thread = threading.Thread(target=self._read_stderr_loop, daemon=True)
        self._stderr_thread.start()

        # MCP handshake.
        self._call("initialize", {
            "protocolVersion": MCP_PROTOCOL_VERSION,
            "capabilities": {},
            "clientInfo": {"name": "soliton-mcp-shim", "version": "0.1.0"},
        })
        self._notify("notifications/initialized", {})

    def stop(self) -> None:
        if self._proc is None:
            return
        try:
            self._proc.stdin.close()  # type: ignore[union-attr]
        except Exception:
            pass
        try:
            self._proc.wait(timeout=2)
        except subprocess.TimeoutExpired:
            self._proc.kill()
        self._proc = None

    def call_tool(self, name: str, arguments: dict | None = None) -> dict:
        """Invoke an MCP tool. Returns the tool result content (typically a list of
        content items; first text item is JSON-decoded if possible)."""
        result = self._call("tools/call", {
            "name": name,
            "arguments": arguments or {},
        })
        # MCP convention: result.content = [{type, text}, ...]
        content = result.get("content", [])
        for item in content:
            if item.get("type") == "text":
                txt = item.get("text", "")
                try:
                    return json.loads(txt)
                except json.JSONDecodeError:
                    return {"raw": txt}
        return result

    def _call(self, method: str, params: dict) -> dict:
        if self._proc is None:
            raise McpServerError("client not started")
        with self._lock:
            req_id = self._next_id
            self._next_id += 1
            event = threading.Event()
            self._pending[req_id] = {"event": event}
        msg = {"jsonrpc": JSONRPC, "id": req_id, "method": method, "params": params}
        self._send(msg)
        if not event.wait(timeout=30):
            raise McpServerError(f"timeout waiting for response to {method}")
        with self._lock:
            slot = self._pending.pop(req_id)
        if "error" in slot:
            raise McpServerError(f"{method}: {slot['error']}")
        return slot.get("result", {})

    def _notify(self, method: str, params: dict) -> None:
        self._send({"jsonrpc": JSONRPC, "method": method, "params": params})

    def _send(self, msg: dict) -> None:
        line = json.dumps(msg) + "\n"
        self._proc.stdin.write(line)  # type: ignore[union-attr]
        self._proc.stdin.flush()  # type: ignore[union-attr]

    def _read_stdout_loop(self) -> None:
        assert self._proc is not None
        for line in self._proc.stdout:  # type: ignore[union-attr]
            line = line.strip()
            if not line:
                continue
            try:
                msg = json.loads(line)
            except json.JSONDecodeError:
                continue
            if "id" in msg and ("result" in msg or "error" in msg):
                with self._lock:
                    slot = self._pending.get(msg["id"])
                    if slot is None:
                        continue
                    if "error" in msg:
                        slot["error"] = msg["error"]
                    else:
                        slot["result"] = msg["result"]
                    slot["event"].set()

    def _read_stderr_loop(self) -> None:
        assert self._proc is not None
        for line in self._proc.stderr:  # type: ignore[union-attr]
            self._stderr_lines.append(line.rstrip())

    def stderr_tail(self, n: int = 20) -> list[str]:
        return self._stderr_lines[-n:]


# --- Soliton subcommand routers -----------------------------------------------


def cmd_info(client: CrgMcpClient, args: argparse.Namespace) -> dict:
    """Emit graph stats. Maps to `list_graph_stats_tool` (+ `list_repos_tool` for
    multi-repo registries). Soliton's expected output shape includes
    {nodes, edges, files, languages, last_updated}."""
    stats = client.call_tool("list_graph_stats_tool")
    return stats


def cmd_blast_radius(client: CrgMcpClient, args: argparse.Namespace) -> dict:
    """Maps to `get_impact_radius_tool`. Soliton expects
    {directCallers: [...], transitiveCallers: [...], affectedFiles: [...]}."""
    sym = args.symbol  # form: file.py:func or file.py:Class.method
    file_, _, name = sym.partition(":")
    result = client.call_tool("get_impact_radius_tool", {
        "file": file_,
        "symbol": name,
        "depth": getattr(args, "depth", 2),
    })
    # Translate to Soliton's expected shape. Server returns implementation-defined
    # keys; this is the minimal mapping for v0.
    return {
        "directCallers": result.get("direct_callers", result.get("directCallers", [])),
        "transitiveCallers": result.get("transitive_callers", result.get("transitiveCallers", [])),
        "affectedFiles": result.get("affected_files", result.get("affectedFiles", [])),
    }


def cmd_dependency_breaks(client: CrgMcpClient, args: argparse.Namespace) -> dict:
    """Already covered by the CLI subcommand `code-review-graph detect-changes`;
    the shim shells out rather than going through MCP for this query (CLI
    output is the canonical shape and we don't want a second translation
    layer). Kept here for API symmetry."""
    cmd = ["code-review-graph", "detect-changes"]
    if getattr(args, "base", None):
        cmd += ["--base", args.base]
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0:
        raise McpServerError(f"detect-changes exit {proc.returncode}: {proc.stderr[:200]}")
    return json.loads(proc.stdout)


def cmd_taint_paths(client: CrgMcpClient, args: argparse.Namespace) -> dict:
    """TODO: maps to `traverse_graph_tool` with edge_type=DATA_FLOW. Soliton
    expects [{source, sink, kind, edges, confidence}, ...]. Stubbed."""
    raise NotImplementedError("taint-paths: traverse_graph_tool wiring pending follow-up PR")


def cmd_co_change(client: CrgMcpClient, args: argparse.Namespace) -> dict:
    """TODO: maps to `get_affected_flows_tool` with co-change window param.
    Stubbed."""
    raise NotImplementedError("co-change: get_affected_flows_tool wiring pending follow-up PR")


def cmd_feature_partition(client: CrgMcpClient, args: argparse.Namespace) -> dict:
    """TODO: maps to `list_communities_tool` + `get_community_tool` join.
    Stubbed."""
    raise NotImplementedError("feature-partition: list_communities_tool + get_community_tool wiring pending follow-up PR")


def cmd_review_bundle(client: CrgMcpClient, args: argparse.Namespace) -> dict:
    """TODO: maps to `get_review_context_tool` (composes blast-radius + flows
    + test-coverage server-side). Stubbed."""
    raise NotImplementedError("review-bundle: get_review_context_tool wiring pending follow-up PR")


SUBCOMMANDS = {
    "info": cmd_info,
    "blast-radius": cmd_blast_radius,
    "dependency-breaks": cmd_dependency_breaks,
    "taint-paths": cmd_taint_paths,
    "co-change": cmd_co_change,
    "feature-partition": cmd_feature_partition,
    "review-bundle": cmd_review_bundle,
}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Soliton MCP shim — graph-cli compatible wrapper for code-review-graph")
    parser.add_argument("--graph", type=Path, default=None, help="Repo root for code-review-graph serve --repo")
    sub = parser.add_subparsers(dest="cmd", required=True)

    sub.add_parser("info", help="list_graph_stats_tool")

    p_blast = sub.add_parser("blast-radius", help="get_impact_radius_tool")
    p_blast.add_argument("symbol", help="file.py:func or file.py:Class.method")
    p_blast.add_argument("--depth", type=int, default=2)

    p_dep = sub.add_parser("dependency-breaks", help="detect_changes_tool (or CLI)")
    p_dep.add_argument("--base", default="HEAD~1")

    sub.add_parser("taint-paths", help="traverse_graph_tool DATA_FLOW (stubbed)")
    sub.add_parser("co-change", help="get_affected_flows_tool (stubbed)")
    sub.add_parser("feature-partition", help="list/get_community_tool (stubbed)")
    sub.add_parser("review-bundle", help="get_review_context_tool (stubbed)")

    args = parser.parse_args(argv)

    handler = SUBCOMMANDS.get(args.cmd)
    if handler is None:
        parser.error(f"unknown subcommand: {args.cmd}")

    # `dependency-breaks` doesn't need the MCP client; route directly.
    if args.cmd == "dependency-breaks":
        try:
            print(json.dumps(handler(None, args), indent=2))  # type: ignore[arg-type]
            return 0
        except McpServerError as e:
            print(f"error: {e}", file=sys.stderr)
            return 1

    client = CrgMcpClient(repo=args.graph)
    try:
        client.start()
        result = handler(client, args)
        print(json.dumps(result, indent=2))
        return 0
    except NotImplementedError as e:
        print(f"shim TODO: {e}", file=sys.stderr)
        return 2
    except McpServerError as e:
        print(f"mcp error: {e}", file=sys.stderr)
        for line in client.stderr_tail():
            print(f"  server stderr: {line}", file=sys.stderr)
        return 1
    finally:
        client.stop()


if __name__ == "__main__":
    sys.exit(main())
