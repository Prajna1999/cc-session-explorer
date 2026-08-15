"""DeepSeek Harness (DSH) session export for the CC explorer.

The explorer reads Claude Code session JSONL under ``~/.claude/projects/<slug>/``.
DeepSeek Harness stores its sessions elsewhere and in a different schema:
zstd-compressed streaming JSONL under ``~/.dsh/sessions/<slug>/session-<id>/``
(events like ``user/message``, ``assistant/message``, ``tool/call``,
``tool/result``, plus noisy ``*-chunks`` streaming events).

``cc-commit-context dsh-export`` converts those into Claude Code-format JSONL in
the explorer's tree (``~/.claude/projects/<slug>/<session-id>.jsonl``), so DSH
sessions appear in the local explorer and upload via ``cc-cloud sync --sessions``
— with zero changes to the shared ``parser.py`` (single source of truth).

Dependency-free: decompression shells out to the ``zstd`` CLI.
"""

import json
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

DEFAULT_DSH_ROOT = Path.home() / ".dsh" / "sessions"
DEFAULT_OUT_ROOT = Path.home() / ".claude" / "projects"

# Claude Code block types the shared parser understands
_BLOCK_TEXT = "text"
_BLOCK_THINKING = "thinking"


def _iso(ms: int) -> str:
    return datetime.fromtimestamp(ms / 1000, tz=timezone.utc).isoformat()


def find_sessions(dsh_root: Path) -> list[Path]:
    if not dsh_root.is_dir():
        return []
    return sorted(dsh_root.glob("*/session-*/session.jsonl.zstd"))


def _session_header(path: Path) -> dict | None:
    p = subprocess.run(["zstd", "-dc", str(path)], capture_output=True)
    if p.returncode != 0:
        return None
    first = p.stdout.split(b"\n", 1)[0]
    try:
        obj = json.loads(first)
    except json.JSONDecodeError:
        return None
    if obj.get("type") != "session":
        return None
    return {"id": obj.get("id"), "cwd": obj.get("cwd"), "createdAt": obj.get("createdAt")}


def _text_blocks(content) -> list[str]:
    """Pull plain text out of a DSH content list (blocks of type text/...)."""
    texts = []
    if isinstance(content, str):
        texts.append(content)
    elif isinstance(content, list):
        for block in content:
            if not isinstance(block, dict):
                continue
            if block.get("type") == _BLOCK_TEXT and block.get("text"):
                texts.append(block["text"])
    return texts


def convert_lines(path: Path) -> list[dict]:
    """Stream the DSH session file and map its events to Claude Code JSONL lines."""
    lines: list[dict] = []
    title: str | None = None
    model: str | None = None

    p = subprocess.run(["zstd", "-dc", str(path)], capture_output=True)
    if p.returncode != 0:
        raise RuntimeError(f"zstd failed for {path}: {p.stderr.decode(errors='replace')[:200]}")

    for raw in p.stdout.splitlines():
        try:
            obj = json.loads(raw)
        except json.JSONDecodeError:
            continue
        t = obj.get("type")
        ts = _iso(obj.get("time")) if obj.get("time") else None

        if t == "session/title" and obj.get("data", {}).get("title"):
            title = obj["data"]["title"]
        elif t == "request/header":
            model = (obj.get("data", {}).get("header", {}).get("config", {})).get("model") or model
        elif t == "user/message":
            texts = _text_blocks(obj.get("data", {}).get("content"))
            if texts:
                lines.append({"type": "user", "timestamp": ts,
                              "message": {"content": "\n".join(texts)}})
        elif t == "assistant/message":
            blocks = []
            for block in obj.get("data", {}).get("message", {}).get("content") or []:
                if not isinstance(block, dict):
                    continue
                bt = block.get("type")
                if bt == _BLOCK_TEXT and block.get("text"):
                    blocks.append({"type": "text", "text": block["text"]})
                elif bt in ("reasoning", _BLOCK_THINKING) and block.get("text"):
                    blocks.append({"type": "thinking", "thinking": block["text"]})
            if blocks:
                lines.append({"type": "assistant", "timestamp": ts,
                              "message": {"model": model or "deepseek-v4-flash",
                                          "content": blocks}})
        elif t == "tool/call":
            data = obj.get("data", {})
            try:
                inp = json.loads(data.get("arguments") or "{}")
            except json.JSONDecodeError:
                inp = {"raw": data.get("arguments")}
            lines.append({"type": "assistant", "timestamp": ts,
                          "message": {"model": model or "deepseek-v4-flash",
                                      "content": [{"type": "tool_use",
                                                   "id": data.get("callId"),
                                                   "name": data.get("name"),
                                                   "input": inp}]}})
        elif t == "tool/result":
            call_id = None
            result_texts: list[str] = []
            for block in obj.get("data", {}).get("message", {}).get("content") or []:
                if not isinstance(block, dict):
                    continue
                if block.get("type") == "tool-result":
                    call_id = block.get("toolCallId")
                    result_texts.extend(_text_blocks(block.get("content")))
            if call_id:
                lines.append({"type": "user", "timestamp": ts, "message": {
                    "content": [{"type": "tool_result", "tool_use_id": call_id,
                                 "content": [{"type": "text", "text": txt}
                                             for txt in result_texts]}]}})
        elif t == "compaction/summary":
            lines.append({"type": "system", "timestamp": ts,
                          "subtype": "compact_boundary"})

    # Title first, so it lands in the 64KB head the explorer lists from.
    if title:
        lines.insert(0, {"type": "ai-title", "aiTitle": title})
    return lines


def export_sessions(dsh_root: Path = DEFAULT_DSH_ROOT,
                    out_root: Path = DEFAULT_OUT_ROOT) -> list[dict]:
    """Convert every DSH session into the explorer's tree. Returns summaries."""
    results = []
    for src in find_sessions(dsh_root):
        header = _session_header(src)
        if not header or not header.get("cwd"):
            results.append({"session": src.name, "error": "no session header"})
            continue
        slug = header["cwd"].replace("/", "-")
        out_dir = out_root / slug
        out_dir.mkdir(parents=True, exist_ok=True)
        out_file = out_dir / f"{header['id']}.jsonl"
        try:
            lines = convert_lines(src)
        except RuntimeError as e:
            results.append({"session": header["id"], "error": str(e)})
            continue
        if not lines:
            results.append({"session": header["id"], "skipped": "empty session"})
            continue
        out_file.write_text("\n".join(json.dumps(l) for l in lines) + "\n")
        results.append({
            "session": header["id"],
            "project": slug,
            "cwd": header["cwd"],
            "lines": len(lines),
            "out": str(out_file),
        })
    return results


def run(argv=None) -> int:
    import argparse

    parser = argparse.ArgumentParser(prog="cc-commit-context dsh-export",
                                     description="Export DeepSeek Harness sessions into the explorer's ~/.claude/projects tree")
    parser.add_argument("--dsh-root", default=str(DEFAULT_DSH_ROOT),
                        help="DSH sessions root (default ~/.dsh/sessions)")
    parser.add_argument("--out-root", default=str(DEFAULT_OUT_ROOT),
                        help="explorer projects root (default ~/.claude/projects)")
    args = parser.parse_args(argv)

    if shutil.which("zstd") is None:
        print("error: 'zstd' CLI not found on PATH (needed to decompress DSH sessions)",
              file=sys.stderr)
        return 1

    results = export_sessions(Path(args.dsh_root), Path(args.out_root))
    if not results:
        print("no DSH sessions found")
        return 0
    for r in results:
        if "error" in r:
            print(f"  error  {r['session']}: {r['error']}")
        elif "skipped" in r:
            print(f"  skip   {r['session']}: {r['skipped']}")
        else:
            print(f"  ok     {r['session']} -> {r['out']} ({r['lines']} lines)")
    return 0


if __name__ == "__main__":
    sys.exit(run())
