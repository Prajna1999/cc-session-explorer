import Link from "next/link"
import type { ReactNode } from "react"
import { cn } from "@/lib/utils"

export function ListContainer({ children, className }: { children: ReactNode; className?: string }) {
  return (
    <div className={cn("overflow-hidden rounded-lg border border-border bg-card shadow-xs", className)}>
      {children}
    </div>
  )
}

export function Row({
  href,
  main,
  side,
  className,
}: {
  href?: string
  main: ReactNode
  side?: ReactNode
  className?: string
}) {
  const content = (
    <>
      <div className="flex min-w-0 flex-col gap-0.5">{main}</div>
      {side && <div className="flex shrink-0 items-center gap-2">{side}</div>}
    </>
  )
  const classes = cn(
    "flex flex-wrap items-center justify-between gap-x-4 gap-y-2 border-b border-border/60 px-4 py-3.5 text-inherit no-underline last:border-b-0",
    href &&
      "transition-colors duration-150 ease-out hover:bg-accent/60 focus-visible:ring-2 focus-visible:ring-ring active:bg-accent",
    className
  )
  if (href) {
    return (
      <Link href={href} className={classes}>
        {content}
      </Link>
    )
  }
  return <div className={classes}>{content}</div>
}

export function RowTitle({ children, mono }: { children: ReactNode; mono?: boolean }) {
  return (
    <span
      className={cn(
        "overflow-hidden text-ellipsis whitespace-nowrap text-[15px] font-semibold tracking-tight text-foreground",
        mono && "font-mono text-sm font-medium"
      )}
    >
      {children}
    </span>
  )
}

export function Empty({ children }: { children: ReactNode }) {
  return <div className="px-6 py-12 text-center text-sm text-muted-foreground">{children}</div>
}
