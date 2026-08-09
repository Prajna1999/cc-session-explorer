"use client"

import { useLayoutEffect, useRef, useState } from "react"
import { useRouter } from "next/navigation"
import type { Project } from "@/lib/sessions"

export function ProjectSwitcher({ projects, current }: { projects: Project[]; current: string | null }) {
  const router = useRouter()
  const measureRef = useRef<HTMLSpanElement>(null)
  const [width, setWidth] = useState<number>()
  const currentLabel = projects.find((p) => p.name === current)?.label ?? ""

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
        style={width ? { width: Math.min(Math.max(width + 28, 60), 260) } : undefined}
        className="cursor-pointer rounded-md border-none bg-transparent py-1 pr-6 pl-1 text-sm font-semibold text-foreground outline-none transition-colors duration-200 ease-out hover:bg-accent"
      >
        {projects.map((p) => (
          <option key={p.name} value={p.name}>
            {p.label}
          </option>
        ))}
      </select>
    </>
  )
}
