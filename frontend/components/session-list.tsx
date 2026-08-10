"use client"

import { useMemo, useState } from "react"
import Link from "next/link"
import { Chip } from "@/components/chip"
import { Badge } from "@/components/ui/badge"
import { ListContainer, Row, RowTitle } from "@/components/row-list"
import type { DateGroup } from "@/lib/sessions"

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
          const haystack = `${s.title} ${s.branch ?? ""} ${s.model ?? ""}`.toLowerCase()
          return haystack.includes(q) && (!branch || s.branch === branch)
        }),
      }))
      .filter((g) => g.sessions.length > 0)
  }, [groups, query, branch])

  return (
    <>
      <div className="mb-5 flex items-center justify-between gap-4">
        <div className="flex items-center gap-3">
          <input
            type="search"
            placeholder="Search sessions…"
            autoComplete="off"
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            className="w-80 rounded-md border bg-card px-3.5 py-2 text-sm outline-none transition-colors duration-200 ease-out focus:border-ring"
          />
          <span className="text-muted-foreground">
            {total} session{total === 1 ? "" : "s"}
          </span>
        </div>
        <div className="flex items-center gap-3">
          <Link href={`/p/${project}/git`}>
            <Badge variant="secondary">History</Badge>
          </Link>
          <select
            aria-label="Branch filter"
            value={branch}
            onChange={(e) => setBranch(e.target.value)}
            className="max-w-[260px] cursor-pointer rounded-md border bg-card px-3 py-1.5 text-sm outline-none transition-colors duration-200 ease-out focus:border-ring"
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
          <div className="mb-2 flex justify-between px-1 text-sm font-semibold text-muted-foreground">
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
                main={<RowTitle>{s.title}</RowTitle>}
                side={
                  <>
                    {s.hasSubagents && <Badge variant="secondary">subagents</Badge>}
                    {s.hasToolResults && <Badge variant="secondary">tool-results</Badge>}
                    {s.branch && <Chip variant="branch">{s.branch}</Chip>}
                    {s.model && <Chip variant="model">{s.model}</Chip>}
                    <span className="min-w-[56px] text-right font-mono text-xs text-muted-foreground">{s.size}</span>
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
