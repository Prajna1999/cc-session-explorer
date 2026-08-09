import { notFound } from "next/navigation"
import { AppShell, Crumb, CrumbSep } from "@/components/app-shell"
import { ListContainer, Row, RowTitle, Empty } from "@/components/row-list"
import { listProjects, listToolResults, NotFoundError } from "@/lib/sessions"

export default async function Page({ params }: { params: Promise<{ project: string; sessionId: string }> }) {
  const { project, sessionId } = await params
  const projects = await listProjects()
  let files: Awaited<ReturnType<typeof listToolResults>>
  try {
    files = await listToolResults(project, sessionId)
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
          <Crumb current>tool-results</Crumb>
        </>
      }
    >
      <ListContainer>
        {files.length ? (
          files.map((f) => (
            <Row
              key={f.name}
              href={`/p/${project}/${sessionId}/tool-results/${f.name}`}
              main={<RowTitle mono>{f.name}</RowTitle>}
              side={<span className="min-w-[56px] text-right font-mono text-xs text-muted-foreground">{f.size}</span>}
            />
          ))
        ) : (
          <Empty>No tool results.</Empty>
        )}
      </ListContainer>
    </AppShell>
  )
}
