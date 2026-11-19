# Specs

One folder per Linear issue, `AA-<n>-<slug>/`, in Kiro's spec format: `requirements.md` (or `bugfix.md`),
`design.md` (feature specs) and `tasks.md`. This repo holds the specs of its own work; work across repos keeps its
spec in the AA-Ecosys root repo.

- Rules, templates and approval: AA-Ecosys `.kiro/specs/README.md`, `skill/aa-linear-issue/templates/spec/`.
- Check: the Spec trace workflow (`.github/workflows/spec-trace.yml`, a caller of the root workflow) checks the spec
  of every issue a PR names, and every spec the PR changes.
- `_archive/` holds specs that are closed, and specs written before the issue format
  (`_archive/tripplanner-mvp/`, the v0.3 MVP spec this app was first built from). Specs ship in this public repo: no
  account ID, resource ID or secret.
