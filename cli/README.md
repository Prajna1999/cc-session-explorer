# commit-context

Attaches the Claude Code agent conversation that produced a commit to that
commit itself, via `git notes`, so it travels with the repo on push/pull.

Install once per machine:

    uv tool install --from ./cli commit-context

Install once per repo clone:

    cc-commit-context install

See the root `CLAUDE.md` for how this fits into cc-session-explorer.
