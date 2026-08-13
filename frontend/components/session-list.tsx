"use client"

import { useMemo, useState } from "react"
import Link from "next/link"
import { Chip } from "@/components/chip"
import { Badge } from "@/components/ui/badge"
import { Initials } from "@/components/avatar"
import { ListContainer, Row, RowTitle } from "@/components/row-list"
import type { DateGroup } from "@/lib/sessions"

const TOOL_LINK =
  "inline-flex h-8 items-center rounded-md border border-border bg-card px-2.5 text-xs font-medium text-muted-foreground no-underline shadow-xs transition-colors duration-150 ease-out hover:border-input hover:text-foreground focus-visible:border-ring focus-visible:ring-2 focus-visible:ring-ring/40"

export function SessionList({
  project,
  groups,
  total,
  localBranches,
  remoteBranches,
}: {
  project: string
  groups: DateGroup[]
  total: number
  localBranches: string[]
  remoteBranches: string[]
}) {
  const [query, setQuery] = useState("")
  const [branch, setBranch] = useState("")

  const filtered = useMemo(() => {
    const q = query.toLowerCase()
    return groups
      .map((g) => ({
        ...g,
        sessions: g.sessions.filter((s) => {
          const haystack = `${s.title} ${s.branch ?? ""} ${s.model ?? ""} ${s.author?.name ?? ""}`.toLowerCase()
          return haystack.includes(q) && (!branch || s.branch === branch)
        }),
      }))
      .filter((g) => g.sessions.length > 0)
  }, [groups, query, branch])

  return (
    <>
      <div className="mb-5 flex flex-wrap items-center justify-between gap-3">
        <div className="flex items-center gap-3">
          <input
            type="search"
            placeholder="Search sessions…"
            aria-label="Search sessions"
            autoComplete="off"
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            className="h-9 w-72 max-w-full rounded-md border border-input bg-card px-3 text-sm text-foreground shadow-xs outline-none transition-colors duration-150 ease-out placeholder:text-muted-foreground focus-visible:border-ring focus-visible:ring-3 focus-visible:ring-ring/30"
          />
          <span className="text-sm text-muted-foreground">
            {total} session{total === 1 ? "" : "s"}
          </span>
        </div>
        <div className="flex items-center gap-2">
          <Link href={`/p/${project}/git`} className={TOOL_LINK}>
            History
          </Link>
          <Link href={`/p/${project}/members`} className={TOOL_LINK}>
            Members
          </Link>
          <select
            aria-label="Branch filter"
            value={branch}
            onChange={(e) => setBranch(e.target.value)}
            className="h-8 max-w-[240px] cursor-pointer rounded-md border border-input bg-card px-2 text-sm text-foreground shadow-xs outline-none transition-colors duration-150 ease-out focus-visible:border-ring focus-visible:ring-2 focus-visible:ring-ring/40"
          >
            <option value="">⑂ All branches</option>
            {localBranches.length > 0 && (
              <optgroup label="Local">
                {localBranches.map((b) => (
                  <option key={b} value={b}>
                    {b}
                  </option>
                ))}
              </optgroup>
            )}
            {remoteBranches.length > 0 && (
              <optgroup label="Remote">
                {remoteBranches.map((b) => (
                  <option key={b} value={b}>
                    {b}
                  </option>
                ))}
              </optgroup>
            )}
          </select>
        </div>
      </div>
      {filtered.map((g) => (
        <section key={g.label} className="mb-5">
          <div className="mb-2 flex items-baseline justify-between px-1 text-xs font-semibold text-muted-foreground">
            <span>{g.label}</span>
            <span>
              {g.sessions.length} session{g.sessions.length === 1 ? "" : "s"}
            </span>
          </div>
          <ListContainer>
            {g.sessions.map((s) => (
              <Row
                key={s.id}
                href={`/p/${project}/${s.id}`}
                main={
                  <>
                    <RowTitle>{s.title}</RowTitle>
                    {s.author?.name && (
                      <span className="flex items-center gap-1.5 text-xs text-muted-foreground">
                        <Initials name={s.author.name} />
                        {s.author.name}
                      </span>
                    )}
                  </>
                }
                side={
                  <>
                    {s.hasSubagents && <Badge variant="secondary">subagents</Badge>}
                    {s.hasToolResults && <Badge variant="secondary">tool-results</Badge>}
                    {s.branch && <Chip variant="branch">{s.branch}</Chip>}
                    {s.model && <Chip variant="model">{s.model}</Chip>}
                    <span className="min-w-[52px] text-right font-mono text-xs tabular-nums text-muted-foreground">
                      {s.size}
                    </span>
                  </>
                }
              />
            ))}
          </ListContainer>
        </section>
      ))}
    </>
  )
}
