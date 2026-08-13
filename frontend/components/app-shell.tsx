import Link from "next/link"
import type { ReactNode } from "react"
import type { Project } from "@/lib/sessions"
import { ProjectSwitcher } from "@/components/project-switcher"
import { LogoutButton } from "@/components/logout-button"

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
      <header className="sticky top-0 z-10 flex min-h-[52px] items-center gap-3 border-b border-border bg-card px-4 sm:px-6">
        <Link
          href="/"
          aria-label="Projects"
          className="font-mono text-lg text-foreground no-underline transition-colors duration-150 ease-out hover:text-muted-foreground focus-visible:ring-2 focus-visible:ring-ring"
        >
          ◆
        </Link>
        <ProjectSwitcher projects={projects} current={currentProject} />
        <nav className="hidden min-w-0 items-center gap-1.5 text-sm sm:flex">
          <span className="text-border">/</span>
          {breadcrumbs}
        </nav>
        <div className="ml-auto">
          <LogoutButton />
        </div>
      </header>
      <main className="mx-auto max-w-[1180px] px-4 py-6 sm:px-6">{children}</main>
    </>
  )
}

export function Crumb({ href, children, current }: { href?: string; children: ReactNode; current?: boolean }) {
  if (current) return <span className="truncate font-semibold text-foreground">{children}</span>
  return (
    <Link
      href={href!}
      className="text-muted-foreground no-underline transition-colors duration-150 ease-out hover:text-foreground focus-visible:ring-2 focus-visible:ring-ring"
    >
      {children}
    </Link>
  )
}

export function CrumbSep() {
  return <span className="text-border">/</span>
}
