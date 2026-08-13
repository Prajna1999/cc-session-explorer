"use client"

import { useMemo, useState } from "react"
import Link from "next/link"
import { cn } from "@/lib/utils"
import { Chip } from "@/components/chip"
import type { Entry, SessionMeta } from "@/lib/sessions"

const CARD = "rounded-lg border border-border bg-card p-3.5 shadow-xs"

function fmtTime(ts: Date | null): string {
  if (!ts) return ""
  return new Date(ts).toLocaleTimeString("en-GB", { hour: "2-digit", minute: "2-digit" })
}

function Msg({ children, className }: { children: string; className?: string }) {
  return (
    <pre className={cn("max-h-[480px] overflow-y-auto whitespace-pre-wrap break-words font-sans text-sm", className)}>
      {children}
    </pre>
  )
}

function Summary({ children, className }: { children: React.ReactNode; className?: string }) {
  return (
    <summary
      className={cn(
        "flex cursor-pointer list-none items-center gap-2 transition-colors duration-150 ease-out focus-visible:ring-2 focus-visible:ring-ring [&::-webkit-details-marker]:hidden",
        className
      )}
    >
      {children}
    </summary>
  )
}

const CARET_CLOSED = "before:content-['▸']"
const CARET_OPEN = "open:mb-2 open:before:content-['▾']"

export function Transcript({
  project,
  sessionId,
  entries,
  meta,
}: {
  project: string
  sessionId: string | null
  entries: Entry[]
  meta: SessionMeta
}) {
  const [off, setOff] = useState<Set<string>>(new Set())

  const toolNames = Object.keys(meta.toolCounts)
  const toolMasterOn = !toolNames.some((n) => off.has(`tool:${n}`))

  function toggle(key: string, checked: boolean) {
    setOff((prev) => {
      const next = new Set(prev)
      if (checked) next.delete(key)
      else next.add(key)
      return next
    })
  }

  function toggleToolMaster(checked: boolean) {
    setOff((prev) => {
      const next = new Set(prev)
      for (const n of toolNames) {
        if (checked) next.delete(`tool:${n}`)
        else next.add(`tool:${n}`)
      }
      return next
    })
  }

  const visible = useMemo(
    () =>
      entries.filter((e) => {
        if (off.has(e.kind)) return false
        if (e.kind === "tool" && off.has(`tool:${e.name}`)) return false
        return true
      }),
    [entries, off]
  )

  return (
    <div className="grid items-start gap-6 md:grid-cols-[minmax(0,1fr)_250px]">
      <div className="flex min-w-0 flex-col gap-2.5">
        {visible.map((e, i) => {
          if (e.kind === "branch") {
            return (
              <div key={i} className="flex items-center gap-2.5 py-1 before:h-px before:flex-1 before:bg-border after:h-px after:flex-1 after:bg-border">
                <span className="whitespace-nowrap rounded-md border border-border bg-muted px-2 py-0.5 font-mono text-xs text-muted-foreground">{e.text}</span>
              </div>
            )
          }
          if (e.kind === "user") {
            return (
              <div key={i} className={cn(CARD, e.meta && "opacity-65")}>
                <div className="mb-1.5 flex items-center gap-2">
                  <span className="text-xs font-semibold text-foreground">User</span>
                  {e.ts && <span className="ml-auto font-mono text-xs tabular-nums text-muted-foreground">{fmtTime(e.ts)}</span>}
                </div>
                <Msg>{e.text}</Msg>
              </div>
            )
          }
          if (e.kind === "assistant") {
            return (
              <div key={i} className={CARD}>
                <div className="mb-1.5 flex flex-wrap items-center gap-2">
                  <span className="text-xs font-semibold text-muted-foreground">Assistant</span>
                  {e.model && <Chip variant="model">{e.model}</Chip>}
                  {e.ts && <span className="ml-auto font-mono text-xs tabular-nums text-muted-foreground">{fmtTime(e.ts)}</span>}
                </div>
                <Msg>{e.text}</Msg>
              </div>
            )
          }
          if (e.kind === "thinking") {
            return (
              <details key={i} className={CARD}>
                <Summary className={cn("text-sm text-muted-foreground hover:text-foreground", CARET_CLOSED, CARET_OPEN)}>
                  Thinking{e.ts && ` · ${fmtTime(e.ts)}`}
                </Summary>
                <Msg className="text-muted-foreground">{e.text}</Msg>
              </details>
            )
          }
          if (e.kind === "tool") {
            return (
              <details key={i} className={CARD}>
                <Summary className={cn(CARET_CLOSED, CARET_OPEN)}>
                  <span className="font-mono text-sm font-semibold">{e.name}</span>
                  {e.arg && <span className="overflow-hidden text-ellipsis whitespace-nowrap font-mono text-xs text-muted-foreground">{e.arg}</span>}
                  {e.ts && <span className="ml-auto font-mono text-xs tabular-nums text-muted-foreground">{fmtTime(e.ts)}</span>}
                </Summary>
                <div className="flex flex-col gap-1.5">
                  {e.params.map((p) => (
                    <div key={p.key} className="grid grid-cols-[110px_1fr] items-start gap-2.5">
                      <span className="pt-2.5 font-mono text-xs text-muted-foreground">{p.key}</span>
                      <Msg className="max-h-40 rounded-md border border-border bg-muted p-2.5 font-mono text-xs">{p.value}</Msg>
                    </div>
                  ))}
                  {e.result && (
                    <>
                      <div className="text-xs font-medium text-muted-foreground">result</div>
                      <Msg className="max-h-80 rounded-md border border-border bg-muted p-2.5 font-mono text-xs">{e.result}</Msg>
                    </>
                  )}
                  {e.persisted && sessionId && (
                    <Link
                      href={`/p/${project}/${sessionId}/tool-results/${e.persisted}`}
                      className="inline-block w-fit rounded-md border border-border px-2 py-0.5 text-xs text-muted-foreground no-underline transition-colors duration-150 ease-out hover:border-input hover:text-foreground focus-visible:border-ring focus-visible:ring-2 focus-visible:ring-ring/40"
                    >
                      full output: {e.persisted} →
                    </Link>
                  )}
                </div>
              </details>
            )
          }
          // system
          return (
            <div key={i} className="px-4 py-0.5 text-center text-xs text-muted-foreground">
              {e.text}
              {e.ts && ` · ${fmtTime(e.ts)}`}
            </div>
          )
        })}
      </div>

      <aside className="sticky top-[68px]">
        <div className={CARD}>
          <div className="mb-2 text-sm font-semibold tracking-tight">View</div>
          <ViewCheck label="Prompts" count={meta.prompts} checked={!off.has("user")} onChange={(c) => toggle("user", c)} />
          <ViewCheck label="Responses" count={meta.responses} checked={!off.has("assistant")} onChange={(c) => toggle("assistant", c)} />
          <ViewCheck label="Thinking" count={meta.thinking} checked={!off.has("thinking")} onChange={(c) => toggle("thinking", c)} />
          <ViewCheck label="Tool calls" count={meta.toolCalls} checked={toolMasterOn} onChange={toggleToolMaster} />
          <div className="ml-5 border-l pl-2.5">
            {toolNames.map((name) => (
              <ViewCheck key={name} label={name} count={meta.toolCounts[name]} checked={!off.has(`tool:${name}`)} onChange={(c) => toggle(`tool:${name}`, c)} />
            ))}
          </div>
          <ViewCheck label="System" checked={!off.has("system")} onChange={(c) => toggle("system", c)} />
        </div>
      </aside>
    </div>
  )
}

function ViewCheck({
  label,
  count,
  checked,
  onChange,
}: {
  label: string
  count?: number
  checked: boolean
  onChange: (checked: boolean) => void
}) {
  return (
    <label className="flex cursor-pointer select-none items-center gap-2 rounded-md px-1 py-1 text-sm transition-colors duration-150 ease-out hover:bg-accent/60">
      <input
        type="checkbox"
        checked={checked}
        onChange={(e) => onChange(e.target.checked)}
        className="size-3.5 accent-signal transition-transform duration-100 ease-out active:scale-90"
      />
      <span className="font-mono text-xs">{label}</span>
      {count !== undefined && <span className="ml-auto font-mono text-xs tabular-nums text-muted-foreground">{count}</span>}
    </label>
  )
}
