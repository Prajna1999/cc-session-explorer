import { notFound } from "next/navigation"
import { AppShell, Crumb, CrumbSep } from "@/components/app-shell"
import { ListContainer, Row, RowTitle, Empty } from "@/components/row-list"
import { Initials } from "@/components/avatar"
import { listProjects, listMembers, NotFoundError, withAuth } from "@/lib/sessions"

export default async function Page({ params }: { params: Promise<{ project: string }> }) {
  const { project } = await params
  const projects = await withAuth(() => listProjects())
  let members: Awaited<ReturnType<typeof listMembers>>
  try {
    members = await withAuth(() => listMembers(project))
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
          <Crumb current>Members</Crumb>
        </>
      }
    >
      <ListContainer>
        {members.length ? (
          members.map((m) => (
            <Row
              key={m.id}
              main={
                <>
                  <RowTitle>{m.name ?? m.email}</RowTitle>
                  <span className="flex items-center gap-1.5 text-xs text-muted-foreground">
                    <Initials name={m.name ?? m.email} />
                    {m.email}
                  </span>
                </>
              }
            />
          ))
        ) : (
          <Empty>No members.</Empty>
        )}
      </ListContainer>
    </AppShell>
  )
}
