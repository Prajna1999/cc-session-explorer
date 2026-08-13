import { cn } from "@/lib/utils"

export function Chip({ variant, children }: { variant: "model" | "branch"; children: React.ReactNode }) {
  return (
    <span
      className={cn(
        "inline-flex items-center whitespace-nowrap rounded-md border px-1.5 py-0.5 font-mono text-xs leading-4",
        variant === "model" && "border-signal/30 bg-signal/10 text-signal",
        variant === "branch" && "border-border bg-muted text-muted-foreground"
      )}
    >
      {children}
    </span>
  )
}
