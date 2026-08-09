import Link from "next/link"
import type { ReactNode } from "react"
import type { Project } from "@/lib/sessions"
import { ProjectSwitcher } from "@/components/project-switcher"

export function AppShell({
  projects,
  currentProject,
  breadcrumbs,
  children,
}: {
  projects: Project[]
  currentProject: string | null
  breadcrumbs: ReactNode
  children: ReactNode
}) {
  return (
    <>
      <header className="sticky top-0 z-10 flex items-center gap-3.5 border-b bg-card px-6 py-3">
        <Link href="/" className="text-lg text-foreground no-underline transition-opacity duration-200 ease-out hover:opacity-70">
          ◆
        </Link>
        <ProjectSwitcher projects={projects} current={currentProject} />
        <nav className="flex items-center gap-2 text-sm">
          <span className="text-border">/</span>
          {breadcrumbs}
        </nav>
      </header>
      <main className="mx-auto max-w-[1180px] px-6 py-6">{children}</main>
    </>
  )
}

export function Crumb({ href, children, current }: { href?: string; children: ReactNode; current?: boolean }) {
  if (current) return <span className="font-semibold text-foreground">{children}</span>
  return (
    <Link href={href!} className="text-muted-foreground no-underline transition-colors duration-200 ease-out hover:text-foreground">
      {children}
    </Link>
  )
}

export function CrumbSep() {
  return <span className="text-border">/</span>
}
