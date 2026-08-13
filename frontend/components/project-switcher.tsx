"use client"

import { useLayoutEffect, useMemo, useRef, useState } from "react"
import { useRouter } from "next/navigation"
import type { Project } from "@/lib/sessions"

export function ProjectSwitcher({ projects, current }: { projects: Project[]; current: string | null }) {
  const router = useRouter()
  const measureRef = useRef<HTMLSpanElement>(null)
  const [width, setWidth] = useState<number>()
  const currentLabel = projects.find((p) => p.name === current)?.label ?? ""

  // Group by team so cloud users can find repos across workspaces.
  const grouped = useMemo(() => {
    const map = new Map<string, Project[]>()
    for (const p of projects) {
      const key = p.teamName ?? "Projects"
      if (!map.has(key)) map.set(key, [])
      map.get(key)!.push(p)
    }
    return [...map.entries()]
  }, [projects])

  useLayoutEffect(() => {
    if (measureRef.current) setWidth(measureRef.current.offsetWidth)
  }, [currentLabel])

  return (
    <>
      <span ref={measureRef} className="invisible absolute whitespace-pre text-sm font-semibold">
        {currentLabel}
      </span>
      <select
        aria-label="Project"
        defaultValue={current ?? ""}
        onChange={(e) => router.push(`/p/${e.target.value}`)}
        style={width ? { width: Math.min(Math.max(width + 40, 90), 280) } : undefined}
        className="cursor-pointer rounded-md border border-border bg-card py-1.5 pr-7 pl-2 text-sm font-semibold text-foreground shadow-xs outline-none transition-colors duration-150 ease-out hover:border-input focus-visible:border-ring focus-visible:ring-2 focus-visible:ring-ring/40"
      >
        {grouped.map(([team, teamProjects]) => (
          <optgroup key={team} label={team}>
            {teamProjects.map((p) => (
              <option key={p.name} value={p.name}>
                {p.label}
              </option>
            ))}
          </optgroup>
        ))}
      </select>
    </>
  )
}
