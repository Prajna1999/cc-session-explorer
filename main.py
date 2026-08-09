import json
import re
import subprocess
from datetime import datetime
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import PlainTextResponse

from commit_context.parser import parse_ts, iter_jsonl, pretty_model, parse_session

ROOT = Path("~/.claude/projects").expanduser()

app = FastAPI()

SAFE_SEGMENT = re.compile(r"^[\w.-]+$")


def safe_path(*parts: str) -> Path:
    if not all(SAFE_SEGMENT.match(p) for p in parts):
        raise HTTPException(404)
    path = ROOT.joinpath(*parts).resolve()
    if not path.is_relative_to(ROOT) or not path.exists():
        raise HTTPException(404)
    return path


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


# ---------- routes ----------

@app.get("/api/projects")
def index():
    return list_projects()


@app.get("/api/projects/{project}")
def project_view(project: str):
    project_dir = safe_path(project)
    sessions = [session_summary(p) for p in project_dir.glob("*.jsonl")]
    sessions.sort(key=lambda s: s["start"] or datetime.fromtimestamp(0).astimezone(), reverse=True)
    cwd = project_cwd(project_dir)
    local, remote = git_branches(cwd)
    session_branches = {s["branch"] for s in sessions if s["branch"]}
    local += sorted(session_branches - set(local) - set(remote))
    return {"groups": group_by_date(sessions), "total": len(sessions),
            "local_branches": local, "remote_branches": remote}


@app.get("/api/projects/{project}/git")
def git_history(project: str):
    project_dir = safe_path(project)
    cwd = project_cwd(project_dir)
    out = run_git(cwd, "log", "--all", "--date=format:%d %b %Y %H:%M",
                  "--format=%H%x1f%D%x1f%an%x1f%ad%x1f%s", "-n", "300")
    commits = []
    for line in out.splitlines():
        parts = line.split("\x1f")
        if len(parts) == 5:
            refs = [r.strip() for r in parts[1].split(",") if r.strip()]
            full_hash = parts[0]
            commits.append({"hash": full_hash[:7], "sha": full_hash, "refs": refs, "author": parts[2],
                            "date": parts[3], "subject": parts[4],
                            "has_context": (project_dir / "commits" / f"{full_hash}.json").exists()})
    return {"cwd": cwd, "commits": commits}


@app.get("/api/projects/{project}/commits/{sha}")
def commit_context(project: str, sha: str):
    path = safe_path(project, "commits", f"{sha}.json")
    return json.loads(path.read_text())


@app.get("/api/projects/{project}/sessions/{session_id}")
def session_view(project: str, session_id: str):
    path = safe_path(project, session_id + ".jsonl")
    entries, meta = parse_session(path)
    sidecar = path.parent / session_id
    return {"entries": entries, "meta": meta,
            "has_subagents": (sidecar / "subagents").is_dir(),
            "has_tool_results": (sidecar / "tool-results").is_dir()}


@app.get("/api/projects/{project}/sessions/{session_id}/subagents")
def subagents_view(project: str, session_id: str):
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
    return agents


@app.get("/api/projects/{project}/sessions/{session_id}/subagents/{agent_id}")
def subagent_view(project: str, session_id: str, agent_id: str):
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
    return {"entries": entries, "meta": meta}


@app.get("/api/projects/{project}/sessions/{session_id}/tool-results")
def tool_results_view(project: str, session_id: str):
    results_dir = safe_path(project, session_id, "tool-results")
    return [{"name": p.name, "size": fmt_size(p.stat().st_size)}
            for p in sorted(results_dir.iterdir()) if p.is_file()]


@app.get("/api/projects/{project}/sessions/{session_id}/tool-results/{name}")
def tool_result_file(project: str, session_id: str, name: str):
    path = safe_path(project, session_id, "tool-results", name)
    return PlainTextResponse(path.read_text(errors="replace"))
