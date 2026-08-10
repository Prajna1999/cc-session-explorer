import { notFound } from "next/navigation"
import { AppShell, Crumb, CrumbSep } from "@/components/app-shell"
import { Chip } from "@/components/chip"
import { Transcript } from "@/components/transcript"
import { listProjects, getCommitContext, NotFoundError } from "@/lib/sessions"

export default async function Page({ params }: { params: Promise<{ project: string; sha: string }> }) {
  const { project, sha } = await params
  const projects = await listProjects()
  let commit: Awaited<ReturnType<typeof getCommitContext>>
  try {
    commit = await getCommitContext(project, sha)
  } catch (e) {
    if (e instanceof NotFoundError) notFound()
    throw e
  }
  const { entries, meta, sessionId } = commit
  const shortSha = sha.slice(0, 7)

  return (
    <AppShell
      projects={projects}
      currentProject={project}
      breadcrumbs={
        <>
          <Crumb href={`/p/${project}`}>Sessions</Crumb>
          <CrumbSep />
          <Crumb href={`/p/${project}/git`}>History</Crumb>
          <CrumbSep />
          <Crumb current>{shortSha}</Crumb>
        </>
      }
    >
      <div className="mb-5">
        <h1 className="mb-2 text-2xl font-bold tracking-tight text-foreground">Commit context · {shortSha}</h1>
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
        </div>
        <div className="mb-1 flex flex-wrap items-center gap-2 text-[13px]">
          <span className="font-mono text-xs text-muted-foreground">
            {sha} · from session {sessionId}
          </span>
        </div>
      </div>

      <Transcript project={project} sessionId={sessionId} entries={entries} meta={meta} />
    </AppShell>
  )
}
