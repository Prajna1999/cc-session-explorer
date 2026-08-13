import json
import re
from datetime import datetime
from pathlib import Path

RESULT_LIMIT = 2000
PARAM_LIMIT = 600
TOOL_ARG_KEYS = ("file_path", "command", "path", "pattern", "query", "url", "skill", "description", "prompt")


def parse_ts(raw):
    if not raw:
        return None
    try:
        return datetime.fromisoformat(raw.replace("Z", "+00:00")).astimezone()
    except ValueError:
        return None


def iter_jsonl(data: bytes):
    for line in data.splitlines():
        line = line.strip()
        if line:
            try:
                yield json.loads(line)
            except (json.JSONDecodeError, UnicodeDecodeError):
                continue


def pretty_model(m: str) -> str:
    parts = [p for p in m.removeprefix("claude-").split("-") if not (p.isdigit() and len(p) == 8)]
    if not parts:
        return m
    version = ".".join(parts[1:])
    return (parts[0].title() + " " + version).strip()


def tool_arg(inp: dict) -> str:
    for key in TOOL_ARG_KEYS:
        val = inp.get(key)
        if isinstance(val, str) and val.strip():
            return val.strip().splitlines()[0][:90]
    return ""


def tool_params(inp: dict) -> list[dict]:
    params = []
    for key, val in inp.items():
        if not isinstance(val, str):
            val = json.dumps(val, indent=2)
        val = val.strip()
        if not val:
            continue
        if len(val) > PARAM_LIMIT:
            val = val[:PARAM_LIMIT] + " …"
        params.append({"key": key, "value": val})
    return params


def result_text(block: dict) -> str:
    content = block.get("content")
    if isinstance(content, list):
        content = "\n".join(b.get("text", "") for b in content if isinstance(b, dict) and b.get("type") == "text")
    if not isinstance(content, str):
        content = json.dumps(content) if content else ""
    content = content.strip()
    if len(content) > RESULT_LIMIT:
        content = content[:RESULT_LIMIT] + f"\n… truncated ({len(content)} chars total)"
    return content


def parse_session(path: Path) -> tuple[list, dict]:
    entries: list[dict] = []
    by_tool_id = {}
    meta = {"title": None, "cwd": None, "version": None, "models": [], "branches": [],
            "first": None, "last": None, "prompts": 0, "tool_calls": 0,
            "tool_counts": {}, "thinking": 0, "responses": 0}
    branch = None
    # ponytail: full parse per request, no cache — largest file is ~3.6MB; add mtime-keyed cache if slow
    with path.open("rb") as f:
        for raw in f:
            try:
                obj = json.loads(raw)
            except (json.JSONDecodeError, UnicodeDecodeError):
                continue
            t = obj.get("type")
            ts = parse_ts(obj.get("timestamp"))
            if ts:
                meta["first"] = meta["first"] or ts
                meta["last"] = ts
            meta["version"] = meta["version"] or obj.get("version")
            meta["cwd"] = meta["cwd"] or obj.get("cwd")
            if t == "ai-title" and obj.get("aiTitle"):
                meta["title"] = obj["aiTitle"]
            b = obj.get("gitBranch")
            if b and b != branch:
                branch = b
                if b not in meta["branches"]:
                    meta["branches"].append(b)
                entries.append({"kind": "branch", "text": b, "ts": ts})

            if t == "user":
                content = (obj.get("message") or {}).get("content")
                persisted = obj.get("toolUseResult") if isinstance(obj.get("toolUseResult"), dict) else {}
                persisted_name = Path(persisted["persistedOutputPath"]).name if persisted.get("persistedOutputPath") else None
                texts = []
                if isinstance(content, str):
                    texts.append(content)
                elif isinstance(content, list):
                    for block in content:
                        if not isinstance(block, dict):
                            continue
                        if block.get("type") == "tool_result":
                            call = by_tool_id.get(block.get("tool_use_id"))
                            if call is not None:
                                call["result"] = result_text(block)
                                call["persisted"] = persisted_name
                        elif block.get("type") == "text":
                            texts.append(block.get("text", ""))
                text = "\n".join(x for x in texts if x.strip())
                if text.strip():
                    meta["prompts"] += 1
                    entries.append({"kind": "user", "text": text, "ts": ts, "meta": bool(obj.get("isMeta"))})

            elif t == "assistant":
                msg = obj.get("message") or {}
                model = msg.get("model") or ""
                if model and not model.startswith("<") and model not in meta["models"]:
                    meta["models"].append(model)
                for block in msg.get("content") or []:
                    if not isinstance(block, dict):
                        continue
                    bt = block.get("type")
                    if bt == "text" and block.get("text", "").strip():
                        meta["responses"] += 1
                        entries.append({"kind": "assistant", "text": block["text"], "ts": ts, "model": pretty_model(model)})
                    elif bt == "thinking" and block.get("thinking", "").strip():
                        meta["thinking"] += 1
                        entries.append({"kind": "thinking", "text": block["thinking"], "ts": ts})
                    elif bt == "tool_use":
                        name = block.get("name", "?")
                        meta["tool_calls"] += 1
                        meta["tool_counts"][name] = meta["tool_counts"].get(name, 0) + 1
                        entry = {"kind": "tool", "name": name, "ts": ts,
                                 "arg": tool_arg(block.get("input") or {}),
                                 "params": tool_params(block.get("input") or {}),
                                 "result": None, "persisted": None}
                        by_tool_id[block.get("id")] = entry
                        entries.append(entry)

            elif t == "system":
                sub = obj.get("subtype")
                if sub == "turn_duration":
                    entries.append({"kind": "system", "ts": ts,
                                    "text": f"turn · {obj.get('durationMs', 0) // 1000}s · {obj.get('messageCount', '?')} messages"})
                elif sub == "compact_boundary":
                    entries.append({"kind": "system", "text": "context compacted", "ts": ts})

            elif t == "attachment":
                hook = (obj.get("attachment") or {}).get("hookName")
                if hook:
                    entries.append({"kind": "system", "text": f"hook · {hook}", "ts": ts})

    meta["models"] = [pretty_model(m) for m in meta["models"]]
    meta["tool_counts"] = dict(sorted(meta["tool_counts"].items(), key=lambda kv: -kv[1]))
    if meta["title"]:
        meta["title"] = re.sub(r"<[^>]+>", " ", meta["title"]).strip()[:140]
    return entries, meta
