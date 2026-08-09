import { AppShell, Crumb } from "@/components/app-shell"
import { ListContainer, Row, RowTitle, Empty } from "@/components/row-list"
import { listProjects } from "@/lib/sessions"

export default async function Page() {
  const projects = await listProjects()
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
                  <span className="font-mono text-xs text-muted-foreground">{p.cwd}</span>
                </>
              }
              side={
                <>
                  <span className="text-muted-foreground">
                    {p.count} session{p.count === 1 ? "" : "s"}
                  </span>
                  <span className="text-muted-foreground">
                    {p.last.toLocaleDateString("en-US", { day: "numeric", month: "short", year: "numeric" })}
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
