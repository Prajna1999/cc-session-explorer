import { cn } from "@/lib/utils"

function initials(name: string): string {
  return name
    .split(/\s+/)
    .filter(Boolean)
    .slice(0, 2)
    .map((p) => p[0]!.toUpperCase())
    .join("")
}

/** Tiny initials avatar — the only place the signal accent reads as a fill. */
export function Initials({ name, className }: { name: string; className?: string }) {
  return (
    <span
      aria-hidden
      className={cn(
        "inline-flex size-5 shrink-0 items-center justify-center rounded-md bg-signal/15 font-mono text-[10px] font-semibold text-signal",
        className
      )}
    >
      {initials(name) || "?"}
    </span>
  )
}
