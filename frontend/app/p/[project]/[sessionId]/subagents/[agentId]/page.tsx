import { notFound } from "next/navigation"
import { AppShell, Crumb, CrumbSep } from "@/components/app-shell"
import { Chip } from "@/components/chip"
import { Transcript } from "@/components/transcript"
import { listProjects, getSubagent, NotFoundError, withAuth } from "@/lib/sessions"

export default async function Page({
  params,
}: {
  params: Promise<{ project: string; sessionId: string; agentId: string }>
}) {
  const { project, sessionId, agentId: rawAgentId } = await params
  const agentId = rawAgentId.replace(/^agent-/, "")
  const projects = await withAuth(() => listProjects())
  let subagent: Awaited<ReturnType<typeof getSubagent>>
  try {
    subagent = await withAuth(() => getSubagent(project, sessionId, agentId))
  } catch (e) {
    if (e instanceof NotFoundError) notFound()
    throw e
  }
  const { entries, meta } = subagent
  const title = meta.title || sessionId

  return (
    <AppShell
      projects={projects}
      currentProject={project}
      breadcrumbs={
        <>
          <Crumb href={`/p/${project}`}>Sessions</Crumb>
          <CrumbSep />
          <Crumb href={`/p/${project}/${sessionId}/subagents`}>subagents</Crumb>
          <CrumbSep />
          <Crumb current>{title.slice(0, 64)}</Crumb>
        </>
      }
    >
      <div className="mb-5">
        <h1 className="mb-2 text-2xl font-bold tracking-tight text-foreground">{title}</h1>
        <div className="mb-1 flex flex-wrap items-center gap-2 text-[13px]">
          {meta.models.map((m) => (
            <Chip key={m} variant="model">
              {m}
            </Chip>
          ))}
          {meta.branches.map((b) => (
            <Chip key={b} variant="branch">
              {b}
            </Chip>
          ))}
          {meta.first && (
            <span className="text-muted-foreground">
              {new Date(meta.first).toLocaleString("en-GB", { day: "numeric", month: "short", year: "numeric", hour: "2-digit", minute: "2-digit" })}
              {meta.last && meta.last !== meta.first && ` → ${new Date(meta.last).toLocaleTimeString("en-GB", { hour: "2-digit", minute: "2-digit" })}`}
            </span>
          )}
          <span className="text-muted-foreground">
            · {meta.prompts} prompts · {meta.toolCalls} tool calls
          </span>
          {meta.version && <span className="font-mono text-muted-foreground">· v{meta.version}</span>}
        </div>
        <div className="mb-1 flex flex-wrap items-center gap-2 text-[13px]">
          <span className="font-mono text-xs text-muted-foreground">
            {sessionId}
            {meta.cwd && ` · ${meta.cwd}`}
          </span>
        </div>
      </div>

      <Transcript project={project} sessionId={sessionId} entries={entries} meta={meta} />
    </AppShell>
  )
}
