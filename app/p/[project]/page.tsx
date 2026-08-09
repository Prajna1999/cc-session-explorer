import { notFound } from "next/navigation"
import { AppShell, Crumb } from "@/components/app-shell"
import { SessionList } from "@/components/session-list"
import { listProjects, projectSessions, NotFoundError } from "@/lib/sessions"

export default async function Page({ params }: { params: Promise<{ project: string }> }) {
  const { project } = await params
  const projects = await listProjects()
  let sessions: Awaited<ReturnType<typeof projectSessions>>
  try {
    sessions = await projectSessions(project)
  } catch (e) {
    if (e instanceof NotFoundError) notFound()
    throw e
  }
  const { groups, total, localBranches, remoteBranches } = sessions
  return (
    <AppShell projects={projects} currentProject={project} breadcrumbs={<Crumb current>Sessions</Crumb>}>
      <SessionList project={project} groups={groups} total={total} localBranches={localBranches} remoteBranches={remoteBranches} />
    </AppShell>
  )
}
