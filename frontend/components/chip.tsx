import { cn } from "@/lib/utils"

export function Chip({ variant, children }: { variant: "model" | "branch"; children: React.ReactNode }) {
  return (
    <span
      className={cn(
        "whitespace-nowrap rounded-md px-2 py-0.5 font-mono text-xs",
        variant === "model" && "bg-orange-100 text-orange-700 dark:bg-orange-950 dark:text-orange-400",
        variant === "branch" && "bg-muted text-muted-foreground"
      )}
    >
      {children}
    </span>
  )
}
