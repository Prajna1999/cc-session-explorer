import { AppShell, Crumb } from "@/components/app-shell"
import { ListContainer, Row, RowTitle, Empty } from "@/components/row-list"
import { listProjects, withAuth } from "@/lib/sessions"

export default async function Page() {
  const projects = await withAuth(() => listProjects())
  return (
    <AppShell projects={projects} currentProject={projects[0]?.name ?? null} breadcrumbs={<Crumb current>Projects</Crumb>}>
      <ListContainer>
        {projects.length ? (
          projects.map((p) => (
            <Row
              key={p.name}
              href={`/p/${p.name}`}
              main={
                <>
                  <RowTitle>{p.label}</RowTitle>
                  <span className="flex items-center gap-2 text-xs text-muted-foreground">
                    {p.teamName && <span>{p.teamName}</span>}
                    <span className="font-mono">{p.cwd}</span>
                  </span>
                </>
              }
              side={
                <>
                  <span className="text-muted-foreground">
                    {p.count} session{p.count === 1 ? "" : "s"}
                  </span>
                  <span className="text-muted-foreground">
                    {p.last
                      ? p.last.toLocaleDateString("en-US", { day: "numeric", month: "short", year: "numeric" })
                      : "no sessions yet"}
                  </span>
                </>
              }
            />
          ))
        ) : (
          <Empty>No projects found.</Empty>
        )}
      </ListContainer>
    </AppShell>
  )
}
