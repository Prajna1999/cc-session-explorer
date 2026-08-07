import json
import re
import subprocess
from datetime import datetime
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import PlainTextResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

ROOT = Path("~/.claude/projects").expanduser()
BASE = Path(__file__).parent

app = FastAPI()
app.mount("/static", StaticFiles(directory=BASE / "static"), name="static")
templates = Jinja2Templates(directory=BASE / "templates")

SAFE_SEGMENT = re.compile(r"^[\w.-]+$")
RESULT_LIMIT = 2000
PARAM_LIMIT = 600


def safe_path(*parts: str) -> Path:
    if not all(SAFE_SEGMENT.match(p) for p in parts):
        raise HTTPException(404)
    path = ROOT.joinpath(*parts).resolve()
    if not path.is_relative_to(ROOT) or not path.exists():
        raise HTTPException(404)
    return path


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


def fmt_size(n: int) -> str:
    return f"{n / 1024:.0f} KB" if n < 1024 * 1024 else f"{n / 1024 / 1024:.1f} MB"


# ---------- git ----------

def run_git(cwd, *args: str) -> str:
    if not cwd or not Path(cwd).is_dir():
        return ""
    try:
        out = subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True, timeout=5)
        return out.stdout if out.returncode == 0 else ""
    except (OSError, subprocess.TimeoutExpired):
        return ""


def git_branches(cwd) -> tuple[list[str], list[str]]:
    local = [b.strip() for b in run_git(cwd, "branch", "--format=%(refname:short)").splitlines() if b.strip()]
    remote = [b.strip() for b in run_git(cwd, "branch", "-r", "--format=%(refname:short)").splitlines()
              if b.strip() and "HEAD" not in b]
    return local, remote


# ---------- projects ----------

def project_cwd(project_dir: Path):
    files = sorted(project_dir.glob("*.jsonl"), key=lambda p: p.stat().st_mtime, reverse=True)
    for p in files[:5]:
        with p.open("rb") as f:
            cwd = next((o["cwd"] for o in iter_jsonl(f.read(262144)) if o.get("cwd")), None)
        if cwd:
            return cwd
    return None


def list_projects() -> list[dict]:
    projects = []
    for d in ROOT.iterdir():
        if not d.is_dir():
            continue
        sessions = list(d.glob("*.jsonl"))
        if not sessions:
            continue
        cwd = project_cwd(d) or d.name
        projects.append({"name": d.name, "cwd": cwd, "label": cwd.rsplit("/", 1)[-1],
                         "count": len(sessions),
                         "last": datetime.fromtimestamp(max(p.stat().st_mtime for p in sessions))})
    projects.sort(key=lambda p: p["last"], reverse=True)
    return projects


def render(request: Request, template: str, ctx: dict):
    projects = list_projects()
    current = ctx.get("project") or (projects[0]["name"] if projects else None)
    return templates.TemplateResponse(request, template, {**ctx, "projects": projects, "current_project": current})


# ---------- session listing ----------

def session_summary(path: Path) -> dict:
    size = path.stat().st_size
    sidecar = path.parent / path.stem
    info = {
        "id": path.stem, "title": None, "last_prompt": None, "first_prompt": None,
        "start": None, "branch": None, "model": None, "size": fmt_size(size),
        "has_subagents": (sidecar / "subagents").is_dir(),
        "has_tool_results": (sidecar / "tool-results").is_dir(),
    }
    with path.open("rb") as f:
        head = f.read(65536)
        tail = b""
        if size > 65536:
            f.seek(max(size - 32768, 0))
            tail = f.read()
            tail = tail[tail.find(b"\n") + 1:]
    for obj in list(iter_jsonl(head)) + list(iter_jsonl(tail)):
        t = obj.get("type")
        info["start"] = info["start"] or parse_ts(obj.get("timestamp"))
        info["branch"] = info["branch"] or obj.get("gitBranch")
        if t == "ai-title" and obj.get("aiTitle"):
            info["title"] = obj["aiTitle"]  # last occurrence wins
        elif t == "last-prompt" and obj.get("lastPrompt"):
            info["last_prompt"] = obj["lastPrompt"]
        elif t == "assistant":
            model = (obj.get("message") or {}).get("model") or ""
            if model and not model.startswith("<"):
                info["model"] = model
        elif t == "user" and not info["first_prompt"] and not obj.get("isMeta"):
            content = (obj.get("message") or {}).get("content")
            if isinstance(content, str) and content.strip():
                info["first_prompt"] = content.strip()
    title = info["title"] or info["first_prompt"] or info["last_prompt"] or info["id"]
    info["title"] = re.sub(r"<[^>]+>", " ", title).strip()[:140] or info["id"]
    if info["model"]:
        info["model"] = pretty_model(info["model"])
    return info


def group_by_date(sessions):
    groups = []
    for s in sessions:
        label = s["start"].strftime("%A %-d %b %Y") if s["start"] else "Unknown date"
        if not groups or groups[-1]["label"] != label:
            groups.append({"label": label, "sessions": []})
        groups[-1]["sessions"].append(s)
    return groups


# ---------- transcript parsing ----------

TOOL_ARG_KEYS = ("file_path", "command", "path", "pattern", "query", "url", "skill", "description", "prompt")


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


# ---------- routes ----------

@app.get("/")
def index(request: Request):
    return render(request, "index.html", {})


@app.get("/p/{project}")
def project_view(request: Request, project: str):
    project_dir = safe_path(project)
    sessions = [session_summary(p) for p in project_dir.glob("*.jsonl")]
    sessions.sort(key=lambda s: s["start"] or datetime.fromtimestamp(0).astimezone(), reverse=True)
    cwd = project_cwd(project_dir)
    local, remote = git_branches(cwd)
    session_branches = {s["branch"] for s in sessions if s["branch"]}
    local += sorted(session_branches - set(local) - set(remote))
    return render(request, "project.html", {
        "project": project, "groups": group_by_date(sessions), "total": len(sessions),
        "local_branches": local, "remote_branches": remote})


@app.get("/p/{project}/git")
def git_history(request: Request, project: str):
    project_dir = safe_path(project)
    cwd = project_cwd(project_dir)
    out = run_git(cwd, "log", "--all", "--date=format:%d %b %Y %H:%M",
                  "--format=%h%x1f%D%x1f%an%x1f%ad%x1f%s", "-n", "300")
    commits = []
    for line in out.splitlines():
        parts = line.split("\x1f")
        if len(parts) == 5:
            refs = [r.strip() for r in parts[1].split(",") if r.strip()]
            commits.append({"hash": parts[0], "refs": refs, "author": parts[2],
                            "date": parts[3], "subject": parts[4]})
    return render(request, "git.html", {"project": project, "cwd": cwd, "commits": commits})


@app.get("/p/{project}/{session_id}")
def session_view(request: Request, project: str, session_id: str):
    path = safe_path(project, session_id + ".jsonl")
    entries, meta = parse_session(path)
    sidecar = path.parent / session_id
    return render(request, "session.html", {
        "project": project, "session_id": session_id, "entries": entries, "meta": meta,
        "is_subagent": False,
        "has_subagents": (sidecar / "subagents").is_dir(),
        "has_tool_results": (sidecar / "tool-results").is_dir()})


@app.get("/p/{project}/{session_id}/subagents")
def subagents_view(request: Request, project: str, session_id: str):
    subagents_dir = safe_path(project, session_id, "subagents")
    agents = []
    for meta_file in sorted(subagents_dir.glob("agent-*.meta.json")):
        try:
            meta = json.loads(meta_file.read_text())
        except (json.JSONDecodeError, OSError):
            meta = {}
        jsonl = meta_file.with_name(meta_file.name.replace(".meta.json", ".jsonl"))
        agents.append({"id": meta_file.name.removeprefix("agent-").removesuffix(".meta.json"),
                       "type": meta.get("agentType", "?"), "description": meta.get("description", ""),
                       "size": fmt_size(jsonl.stat().st_size) if jsonl.exists() else "—"})
    return render(request, "subagents.html", {
        "project": project, "session_id": session_id, "agents": agents})


@app.get("/p/{project}/{session_id}/subagents/{agent_id}")
def subagent_view(request: Request, project: str, session_id: str, agent_id: str):
    agent_id = agent_id.removeprefix("agent-")
    path = safe_path(project, session_id, "subagents", f"agent-{agent_id}.jsonl")
    entries, meta = parse_session(path)
    meta_file = path.with_name(f"agent-{agent_id}.meta.json")
    if meta_file.exists():
        try:
            agent_meta = json.loads(meta_file.read_text())
            meta["title"] = f"{agent_meta.get('agentType', 'agent')} · {agent_meta.get('description', agent_id)}"
        except (json.JSONDecodeError, OSError):
            pass
    return render(request, "session.html", {
        "project": project, "session_id": session_id, "entries": entries, "meta": meta,
        "is_subagent": True, "agent_id": agent_id,
        "has_subagents": False, "has_tool_results": False})


@app.get("/p/{project}/{session_id}/tool-results")
def tool_results_view(request: Request, project: str, session_id: str):
    results_dir = safe_path(project, session_id, "tool-results")
    files = [{"name": p.name, "size": fmt_size(p.stat().st_size)}
             for p in sorted(results_dir.iterdir()) if p.is_file()]
    return render(request, "tool_results.html", {
        "project": project, "session_id": session_id, "files": files})


@app.get("/p/{project}/{session_id}/tool-results/{name}")
def tool_result_file(project: str, session_id: str, name: str):
    path = safe_path(project, session_id, "tool-results", name)
    return PlainTextResponse(path.read_text(errors="replace"))
