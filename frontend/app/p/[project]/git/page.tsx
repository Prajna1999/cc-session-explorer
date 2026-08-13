import { notFound } from "next/navigation"
import { AppShell, Crumb, CrumbSep } from "@/components/app-shell"
import { Chip } from "@/components/chip"
import { Badge } from "@/components/ui/badge"
import { ListContainer, Row, RowTitle, Empty } from "@/components/row-list"
import { listProjects, gitHistory, NotFoundError, withAuth } from "@/lib/sessions"

export default async function Page({ params }: { params: Promise<{ project: string }> }) {
  const { project } = await params
  const projects = await withAuth(() => listProjects())
  let cwd: string | null, commits: Awaited<ReturnType<typeof gitHistory>>["commits"]
  try {
    ;({ cwd, commits } = await withAuth(() => gitHistory(project)))
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
          <Crumb href={`/p/${project}`}>Sessions</Crumb>
          <CrumbSep />
          <Crumb current>History</Crumb>
        </>
      }
    >
      <ListContainer>
        {commits.length ? (
          commits.map((c, i) => (
            <Row
              key={i}
              href={c.hasContext ? `/p/${project}/git/${c.sha}` : undefined}
              main={
                <>
                  <RowTitle>{c.subject}</RowTitle>
                  <span className="text-xs text-muted-foreground">
                    {c.author} · {c.date}
                  </span>
                </>
              }
              side={
                <>
                  {c.hasContext && (
                    <Badge variant="secondary" className="border-signal/30 bg-signal/10 text-signal">
                      context
                    </Badge>
                  )}
                  {c.refs.map((r) => (
                    <Chip key={r} variant="branch">
                      {r}
                    </Chip>
                  ))}
                  <span className="font-mono text-xs text-muted-foreground">{c.hash}</span>
                </>
              }
            />
          ))
        ) : (
          <Empty>No git history — {cwd || "project directory"} is missing or not a git repository.</Empty>
        )}
      </ListContainer>
    </AppShell>
  )
}
