# commit-context

Attaches the Claude Code agent conversation that produced a commit to that
commit itself, via `git notes`, so it travels with the repo on push/pull.

Install once per machine:

    uv tool install --from ./cli commit-context

Install once per repo clone (writes `.git/hooks/post-commit`, `post-merge`,
`post-checkout`, and `pre-push`):

    cc-commit-context install

After that, context is captured for **every** commit, no matter how it's made:

- `git commit` run as a Bash tool call inside a Claude Code session → that
  session's conversation slice, via the `PostToolUse` hook (`capture`).
- commits from a GUI (VS Code, GitHub Desktop), a plain terminal, or another
  tool → the repo's most recently active Claude Code session context, via the
  native `post-commit` hook (`capture-commit`).

Both paths are idempotent and conservative: no session, a stale session (older
than 48h, or one that predates the last captured commit), or nothing new since
the last captured commit means no context is attached.
`git pull`/`git checkout` materialize context that arrived with new commits.

DeepSeek Harness sessions (zstd JSONL under `~/.dsh/sessions/`) can be viewed
in the same explorer by converting them into its tree:

    cc-commit-context dsh-export

This writes `~/.claude/projects/<slug>/<session-id>.jsonl` in the Claude Code
format the explorer (and `cc-cloud sync --sessions`) already reads; re-run it
after any DSH session finishes.

See the root `CLAUDE.md` for how this fits into cc-session-explorer.
