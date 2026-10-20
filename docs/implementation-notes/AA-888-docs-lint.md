# AA-888 — Docs lint workflow in AA-TripPlanner-Web (S245)

## Decisions

- Same shape as AA-Booking `.github/workflows/docs-lint.yml` (#50): front-matter schema, ADR status + index,
  single-line AWS CLI over every tracked Markdown file (`--all`), offline internal links (lychee), unit tests of
  the tools, and a weekly freshness report artifact (`docs-freshness`).
- Tools vendored byte-identical from AA-Ecosys `tools/docs/` (root at #139): `docslib.py`, `check_docs_text.py`,
  `check_adr_index.py`, `check_frontmatter.py`, `test_docs_tools.py`, `lychee.toml`, `requirements.txt`, plus the
  schema `docs/templates/frontmatter.schema.json`. Only `tools/docs/README.md` is repo-specific.
- Root-only `check_alarm_runbooks.py` is not copied: no generated alarm catalog here.
- No resource-ID step in the docs job: `.github/scripts/check_resource_ids.py` already blocks literal IDs in every
  tracked file from the required "Backend (pytest)" job in `ci.yml` (AA-902). `ci.yml` is untouched.
- No path filter, so "Docs lint" reports on every PR and can become a required check later.
- Weekly cron Monday 01:35 UTC, after root (01:17), AA-Booking (01:23) and AA-CIS-Infra (01:29).

## Changed

- `docs/e2e-uat-guide.md`: the bare `http://localhost:3000` in the UAT click-path is now in a code span. Lychee
  reads bare URLs in prose as links and `offline = true` does not skip loopback, so it was the only link failure.

## Tradeoffs

- Fixing the one page instead of adding `exclude_loopback = true` to `lychee.toml`: the toml must stay
  byte-identical in four repos, so a rule change there belongs in a root-first sync, not in this PR.
- No gitleaks step here (AA-Booking has one in Security Audit): out of the issue's "links + ID regex" scope for app
  repos; worth a separate look.

## Should know

- Done-when evidence comes from a throwaway draft PR (branch `test/docs-lint-red`) with an `sg-` ID and a
  backslash-continued `aws` command: expected red on "Docs lint" (AWS CLI) and on "Backend (pytest)" (resource ID).
- Local run: `tools/docs/README.md`. Drift check against the root copy: the `cmp` loop in that README.
