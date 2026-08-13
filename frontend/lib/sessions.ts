import { cookies } from "next/headers"
import { redirect } from "next/navigation"

const API_BASE = process.env.API_BASE_URL ?? "http://localhost:8000"

export const AUTH_COOKIE = "cc_cloud_token"

export class NotFoundError extends Error {}
export class AuthError extends Error {}

async function authToken(): Promise<string | undefined> {
  const store = await cookies()
  return store.get(AUTH_COOKIE)?.value
}

async function authHeaders(): Promise<Record<string, string>> {
  const token = await authToken()
  return token ? { Authorization: `Bearer ${token}` } : {}
}

async function apiGet<T>(path: string): Promise<T> {
  const res = await fetch(`${API_BASE}${path}`, { cache: "no-store", headers: await authHeaders() })
  if (res.status === 401) throw new AuthError()
  if (res.status === 404) throw new NotFoundError()
  if (!res.ok) throw new Error(`Backend request failed: GET ${path} -> ${res.status}`)
  return res.json()
}

/**
 * Runs a data fetch and redirects to /login when the backend says we are
 * unauthenticated (401). Works against both the cloud API (auth required) and
 * the local explorer API (never 401s, so it passes through untouched).
 */
export async function withAuth<T>(fn: () => Promise<T>): Promise<T> {
  try {
    return await fn()
  } catch (e) {
    if (e instanceof AuthError) redirect("/login")
    throw e
  }
}

function reviveDate(s: string | null | undefined): Date | null {
  return s ? new Date(s) : null
}

export interface Author {
  id: string
  name: string | null
  email: string
}

export interface Project {
  name: string
  cwd: string
  label: string
  count: number
  last: Date | null
  teamName: string | null
}

export async function listProjects(): Promise<Project[]> {
  const raw = await apiGet<{
    name: string
    cwd: string
    label: string
    count: number
    last: string | null
    team_name: string | null
  }[]>("/api/projects")
  return raw.map((p) => ({ ...p, last: reviveDate(p.last), teamName: p.team_name }))
}

export interface SessionSummary {
  id: string
  title: string
  branch: string | null
  model: string | null
  size: string
  hasSubagents: boolean
  hasToolResults: boolean
  author: Author | null
}

export interface DateGroup {
  label: string
  sessions: SessionSummary[]
}

export interface ProjectSessions {
  groups: DateGroup[]
  total: number
  localBranches: string[]
  remoteBranches: string[]
}

interface RawSessionSummary {
  id: string
  title: string
  branch: string | null
  model: string | null
  size: string
  has_subagents: boolean
  has_tool_results: boolean
  author: Author | null
}

export async function projectSessions(project: string): Promise<ProjectSessions> {
  const raw = await apiGet<{
    groups: { label: string; sessions: RawSessionSummary[] }[]
    total: number
    local_branches: string[]
    remote_branches: string[]
  }>(`/api/projects/${project}`)
  return {
    groups: raw.groups.map((g) => ({
      label: g.label,
      sessions: g.sessions.map((s) => ({
        id: s.id,
        title: s.title,
        branch: s.branch,
        model: s.model,
        size: s.size,
        hasSubagents: s.has_subagents,
        hasToolResults: s.has_tool_results,
        author: s.author,
      })),
    })),
    total: raw.total,
    localBranches: raw.local_branches,
    remoteBranches: raw.remote_branches,
  }
}

export async function listMembers(project: string): Promise<Author[]> {
  const raw = await apiGet<{ members: Author[] }>(`/api/projects/${project}/members`)
  return raw.members
}

export interface Commit {
  hash: string
  sha: string
  refs: string[]
  author: string | null
  authorEmail: string | null
  date: string | null
  subject: string | null
  hasContext: boolean
}

interface RawCommit {
  hash: string
  sha: string
  refs: string[]
  author: string | null
  author_email: string | null
  date: string | null
  subject: string | null
  has_context: boolean
}

export async function gitHistory(project: string): Promise<{ cwd: string | null; commits: Commit[] }> {
  const raw = await apiGet<{ cwd: string | null; commits: RawCommit[] }>(`/api/projects/${project}/git`)
  return {
    cwd: raw.cwd,
    commits: raw.commits.map((c) => ({
      hash: c.hash,
      sha: c.sha,
      refs: c.refs,
      author: c.author,
      authorEmail: c.author_email,
      date: c.date,
      subject: c.subject,
      hasContext: c.has_context,
    })),
  }
}

export type Entry =
  | { kind: "branch"; text: string; ts: Date | null }
  | { kind: "user"; text: string; ts: Date | null; meta: boolean }
  | { kind: "assistant"; text: string; ts: Date | null; model: string }
  | { kind: "thinking"; text: string; ts: Date | null }
  | {
      kind: "tool"
      name: string
      ts: Date | null
      arg: string
      params: { key: string; value: string }[]
      result: string | null
      persisted: string | null
    }
  | { kind: "system"; text: string; ts: Date | null }

export interface SessionMeta {
  title: string | null
  cwd: string | null
  version: string | null
  models: string[]
  branches: string[]
  first: Date | null
  last: Date | null
  prompts: number
  toolCalls: number
  toolCounts: Record<string, number>
  thinking: number
  responses: number
}

interface RawEntry {
  kind: Entry["kind"]
  ts: string | null
  [key: string]: unknown
}

interface RawSessionMeta {
  title: string | null
  cwd: string | null
  version: string | null
  models: string[]
  branches: string[]
  first: string | null
  last: string | null
  prompts: number
  tool_calls: number
  tool_counts: Record<string, number>
  thinking: number
  responses: number
}

function reviveEntries(raw: RawEntry[]): Entry[] {
  return raw.map((e) => ({ ...e, ts: reviveDate(e.ts) })) as Entry[]
}

function reviveMeta(raw: RawSessionMeta): SessionMeta {
  return {
    title: raw.title,
    cwd: raw.cwd,
    version: raw.version,
    models: raw.models,
    branches: raw.branches,
    first: reviveDate(raw.first),
    last: reviveDate(raw.last),
    prompts: raw.prompts,
    toolCalls: raw.tool_calls,
    toolCounts: raw.tool_counts,
    thinking: raw.thinking,
    responses: raw.responses,
  }
}

export async function getSession(
  project: string,
  sessionId: string
): Promise<{
  entries: Entry[]
  meta: SessionMeta
  hasSubagents: boolean
  hasToolResults: boolean
  author: Author | null
}> {
  const raw = await apiGet<{
    entries: RawEntry[]
    meta: RawSessionMeta
    has_subagents: boolean
    has_tool_results: boolean
    author: Author | null
  }>(`/api/projects/${project}/sessions/${sessionId}`)
  return {
    entries: reviveEntries(raw.entries),
    meta: reviveMeta(raw.meta),
    hasSubagents: raw.has_subagents,
    hasToolResults: raw.has_tool_results,
    author: raw.author,
  }
}

export async function getSubagent(
  project: string,
  sessionId: string,
  agentId: string
): Promise<{ entries: Entry[]; meta: SessionMeta }> {
  const raw = await apiGet<{ entries: RawEntry[]; meta: RawSessionMeta }>(
    `/api/projects/${project}/sessions/${sessionId}/subagents/${agentId}`
  )
  return { entries: reviveEntries(raw.entries), meta: reviveMeta(raw.meta) }
}

export interface SubagentSummary {
  id: string
  type: string
  description: string
  size: string
}

export async function listSubagents(project: string, sessionId: string): Promise<SubagentSummary[]> {
  return apiGet(`/api/projects/${project}/sessions/${sessionId}/subagents`)
}

interface RawCommitMeta {
  models: string[]
  branches: string[]
  first: string | null
  last: string | null
  prompts: number
  tool_calls: number
  tool_counts: Record<string, number>
  thinking: number
  responses: number
}

export async function getCommitContext(
  project: string,
  sha: string
): Promise<{ sessionId: string | null; branch: string | null; entries: Entry[]; meta: SessionMeta }> {
  const raw = await apiGet<{
    session_id: string | null
    branch: string | null
    entries: RawEntry[]
    meta: RawCommitMeta
  }>(`/api/projects/${project}/commits/${sha}`)
  return {
    sessionId: raw.session_id,
    branch: raw.branch,
    entries: reviveEntries(raw.entries),
    meta: reviveMeta({ title: null, cwd: null, version: null, ...raw.meta }),
  }
}

export interface ToolResultFile {
  name: string
  size: string
}

export async function listToolResults(project: string, sessionId: string): Promise<ToolResultFile[]> {
  return apiGet(`/api/projects/${project}/sessions/${sessionId}/tool-results`)
}

export async function toolResultText(project: string, sessionId: string, name: string): Promise<string> {
  const res = await fetch(`${API_BASE}/api/projects/${project}/sessions/${sessionId}/tool-results/${name}`, {
    cache: "no-store",
    headers: await authHeaders(),
  })
  if (res.status === 401) throw new AuthError()
  if (res.status === 404) throw new NotFoundError()
  if (!res.ok) throw new Error(`Backend request failed: GET tool-results/${name} -> ${res.status}`)
  return res.text()
}
