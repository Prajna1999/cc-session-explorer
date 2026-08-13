import { notFound } from "next/navigation"
import { AppShell, Crumb, CrumbSep } from "@/components/app-shell"
import { ListContainer, Row, RowTitle, Empty } from "@/components/row-list"
import { Chip } from "@/components/chip"
import { listProjects, listSubagents, NotFoundError, withAuth } from "@/lib/sessions"

export default async function Page({ params }: { params: Promise<{ project: string; sessionId: string }> }) {
  const { project, sessionId } = await params
  const projects = await withAuth(() => listProjects())
  let agents: Awaited<ReturnType<typeof listSubagents>>
  try {
    agents = await withAuth(() => listSubagents(project, sessionId))
  } catch (e) {
    if (e instanceof NotFoundError) notFound()
    throw e
  }
  return (
    <AppShell
      projects={projects}
      currentProject={project}
      breadcrumbs={
        <>
          <Crumb href="/">Projects</Crumb>
          <CrumbSep />
          <Crumb href={`/p/${project}`}>{project.split("-").pop()}</Crumb>
          <CrumbSep />
          <Crumb href={`/p/${project}/${sessionId}`}>{sessionId.slice(0, 8)}</Crumb>
          <CrumbSep />
          <Crumb current>subagents</Crumb>
        </>
      }
    >
      <ListContainer>
        {agents.length ? (
          agents.map((a) => (
            <Row
              key={a.id}
              href={`/p/${project}/${sessionId}/subagents/${a.id}`}
              main={
                <>
                  <RowTitle>{a.description || a.id}</RowTitle>
                  <span className="font-mono text-xs text-muted-foreground">agent-{a.id}</span>
                </>
              }
              side={
                <>
                  <Chip variant="model">{a.type}</Chip>
                  <span className="min-w-[56px] text-right font-mono text-xs text-muted-foreground">{a.size}</span>
                </>
              }
            />
          ))
        ) : (
          <Empty>No subagents.</Empty>
        )}
      </ListContainer>
    </AppShell>
  )
}
