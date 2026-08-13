# Design — CC Session Explorer (cloud UI)

A locked design system for the explorer's frontend (`frontend/`). Every page
redesign reads this file before emitting code. Do not regenerate per page — amend
this file when the system needs to grow.

## Genre

modern-minimal (developer-tool register — Stripe / Linear / ElevenLabs school)

## Macrostructure family

- **App pages** (every route under `frontend/app/`): **Workbench** — instrument-panel
  header (◆ wordmark · project switcher · breadcrumbs · sign out), bordered list
  surfaces with refined row anatomy, focused detail panes (transcript, git, members).
  Variation knobs: row anatomy per content type, detail-pane layout.

## Theme

**Cobalt** — cool engineered paper, one electric-blue signal accent, monochrome ink.

- Light paper:   `oklch(98.5% 0.004 255)` · Dark paper: `oklch(15% 0.012 260)`
- Ink:           `oklch(17% 0.014 260)` (light) · `oklch(93% 0.006 250)` (dark)
- Rule/border:   `oklch(90.5% 0.008 255)` (light) · `oklch(29% 0.014 260)` (dark)
- Signal accent: `oklch(52% 0.19 255)` (light) · `oklch(66% 0.16 255)` (dark)
- Focus ring:    the signal accent (never animated; appears instantly)
- Radius: 6 px controls (`--radius: 0.375rem`)

Every grey is tinted cool toward the anchor hue — never zero-chroma.

## Typography

- Display + body: **Geist** (`next/font/google`), weights 400 / 500 / 600, display
  tracking `-0.02em` (never italic headers).
- Mono: **Geist Mono** — identifiers, timestamps, code, session ids, hashes.
- 2+1 rule: Geist sans + Geist Mono only. No third face.

## Spacing

Tailwind default 4-pt scale, no custom tokens. Consistent vertical rhythm:
list surfaces `py-3.5` rows, page content `px-6 py-6`, section gaps `mb-5`.

## Motion

- **motion-cut**: transitions on `transform` / `opacity` only, 120–220 ms,
  `--ease-out` (`cubic-bezier(0.16, 1, 0.3, 1)`).
- `prefers-reduced-motion: reduce` collapses spatial motion to ≤ 150 ms opacity
  crossfade.

## Microinteractions stance

- Silent success — no toasts, no confirmation dialogs.
- Hover: 150–200 ms color/border shift. Active press: `translateY(1px)` at 60 ms.
- Focus: visible ring on every interactive element, shown at 0 ms (never animated).
- Rows: `hover:bg-accent`, keyboard `focus-visible` ring.

## CTA voice

- Primary: ink-filled, 6 px radius, `h-9 px-3`, press `translateY(1px)`.
- Secondary / ghost: bordered, hover `bg-accent`.

## Per-page allowances

- App pages MUST NOT use enrichment (heroes, illustration, CSS art) — function
  carries the page.

## What pages MUST share

- The ◆ wordmark (Geist Mono).
- The signal accent's placement: focus rings, active/selected states, selected
  chips only — ≤ 3 % of any viewport.
- Geist + Geist Mono.
- The bordered list-surface language: 1 px border, 6–8 px radius, accent hover.

## What pages MAY differ on

- Row anatomy per content type (session vs commit vs member vs tool-result file).
- Detail-pane layout (transcript two-column grid vs plain list).

## Exports

### shadcn / Tailwind v4 (live in `frontend/app/globals.css`)

```css
:root {
  --background: oklch(98.5% 0.004 255);  /* paper */
  --foreground: oklch(17% 0.014 260);    /* ink */
  --primary: oklch(17% 0.014 260);       /* ink-filled primary */
  --primary-foreground: oklch(98.5% 0.004 255);
  --muted: oklch(95.5% 0.006 255);       /* rule-adjacent surface */
  --muted-foreground: oklch(50% 0.014 260);
  --accent: oklch(94.5% 0.008 255);      /* hover surface */
  --border: oklch(90.5% 0.008 255);      /* rule */
  --input: oklch(90.5% 0.008 255);
  --ring: oklch(52% 0.19 255);           /* signal / focus */
  --radius: 0.375rem;
}
.dark {
  --background: oklch(15% 0.012 260);
  --foreground: oklch(93% 0.006 250);
  --primary: oklch(93% 0.006 250);
  --primary-foreground: oklch(15% 0.012 260);
  --muted: oklch(23.5% 0.014 260);
  --muted-foreground: oklch(68% 0.01 250);
  --accent: oklch(25% 0.015 260);
  --border: oklch(29% 0.014 260);
  --input: oklch(31% 0.015 260);
  --ring: oklch(66% 0.16 255);
}
```

### tokens.css (portable mirror)

```css
:root {
  --color-paper:      oklch(98.5% 0.004 255); --color-paper-dark:  oklch(15% 0.012 260);
  --color-ink:        oklch(17% 0.014 260);   --color-ink-dark:    oklch(93% 0.006 250);
  --color-rule:       oklch(90.5% 0.008 255); --color-rule-dark:   oklch(29% 0.014 260);
  --color-accent:     oklch(52% 0.19 255);    --color-accent-dark: oklch(66% 0.16 255);
  --color-focus:      var(--color-accent);
  --font-display: "Geist", ui-sans-serif, system-ui, sans-serif;
  --font-body:    "Geist", ui-sans-serif, system-ui, sans-serif;
  --font-mono:    "Geist Mono", ui-monospace, monospace;
  --ease-out: cubic-bezier(0.16, 1, 0.3, 1);
  --dur-micro: 120ms; --dur-short: 220ms; --dur-long: 420ms;
  --radius-control: 6px; --radius-surface: 8px;
}
```
