# AA-895 — Dependabot configuration for AA-TripPlanner-Web

Session S243 (19/10/2026), Claude Code. Branch `chore/aa895-dependabot`. Security baseline and vulnerability
process: AA-Ecosys `docs/security/baseline.md` and `docs/security/vulnerability-management.md`.

## Decisions

- Same model as AA-CIS-App (S242): npm (`/frontend`) and pip (`/backend`, the browse and assembly Lambdas share
  `backend/requirements.txt`) get **security updates only**, grouped into one PR per ecosystem.
- `github-actions` gets weekly **version** updates in one grouped PR, like AA-CIS-Infra (AA-867).

## Changed

- New `.github/dependabot.yml`. No workflow or code change.

## Tradeoffs

- Grouping trades one PR per advisory for one PR per week per ecosystem.
- Dependabot PRs get no repository secrets: `vercel-deploy.yml` fails on them (the CI build itself needs no
  secrets). Push the same change from a normal branch when that blocks a merge.

## Should know

- Live state read 19/10/2026: **0 open Dependabot alerts**. Alerts, security updates, secret scanning and push
  protection on; ruleset "main: no force-push, no deletion (AA-867)" active, 2 required checks.
- Lambda packaging (`backend/package_lambdas.sh`) installs from `requirements.txt`, so a merged pip bump reaches
  the Lambdas on the next `deploy-lambdas.yml` run.
