# AA-1096 — settings-lint for TripPlanner ("Settings Lint" CI job)

S279 thresholds audit (root `docs/audits/2026-11-13-S279-session/thresholds-audit/`, section 5.4): each app repo has a
`settings-lint` guard so a new hard-coded threshold, limit or cap fails CI, and the guard is a required check. AA-CIS-App
has it in its unit suite (`tests/unit/test_s279_settings_lint.py`); this repo had only the reader checks of lane L5
(`backend/tests/test_s279_settings.py`).

## Decisions

- **Whole backend, ratchet baseline.** The scan covers every module of `backend/` except the tests, the vendored CIS
  byte copies (`aa_llm/` and `shared/identity_verifier.py`: AA-CIS-App, their owner, lints them, and a marker added here
  would break the byte comparison of `test_vendored_copies.py`) and the CI helper `scripts/`. The 14/11/2026 findings are in
  `backend/tests/fixtures/aa1096_settings_lint_baseline.json` (60 entries), a list that only shrinks: a new constant
  fails, a fixed one must leave the file.
- **Same rule as CIS and CRM:** a module-level UPPER_CASE name that reads like policy (`*_MAX`, `MAX_*`, `*_CAP`,
  `*_LIMIT`, `*_DAYS`, `*_HOURS`, `*_PCT`, `*_THRESHOLD`, `*_USD`, `*_ATTEMPTS`, `*_RETRIES`, `*_TTL`, ...) bound to a
  number, or to a number read from the environment. `config._get` counts as an env reader (it is how `config.py` reads
  every env var).
- **A registry fallback is allowed.** The `backend/config.py` attribute of each `backend.shared.settings.SETTINGS` entry
  is the value served while the CIS view cannot be read; the CIS row wins. Those constants are not findings.
- **Own check name "Settings Lint"** (job `settings-lint` in `.github/workflows/ci.yml`), the same name in AA-Booking and
  AA-Partner, so branch protection can require it. It runs the lint and the lane L5 reader checks; both files also
  stay in "Backend (pytest)".

## Changed

- `backend/tests/test_aa1096_settings_lint.py` (new) and its baseline.
- 10 obvious engineering constants marked `# technical-constant: <reason>` (comment only, no behaviour change), per
  audit section 4 items 6 and 8: the reader cache pace (`shared/app_profile.py`, `shared/embedding_space.py`), the tenant assertion lifetime, key length
  and re-read throttle (`config.py`), the job lease, Lambda time guard, retry backoff and stored error size
  (`jobs/runner.py`, `jobs/queue.py`).
- `.github/workflows/ci.yml`: job "Settings Lint".

## Tradeoffs

- **Duplicate run.** The lint files also run in "Backend (pytest)" (under a second). A job of its own costs about one
  minute of runner time per PR (dependency install from the pip cache) and buys a check name that says what failed and
  that can be required independently.
- **Baseline over classification.** The 60 remaining entries are not classified one by one here (lane L5 moved the
  policy values that mattered). Several are page sizes or job mechanics that deserve the marker; each later PR that
  touches one should mark it or make it a setting, which the shrink check then enforces.

## Should know

- Making "Settings Lint" a required check is branch protection on `main` (a repo-settings step for the orchestrator).
- `TP_DAILY_SPEND_CAP_USD` is baselined: its single source is the app profile (AI gateway › App profiles); the constant
  is the profile's fallback and is not a registry setting.
- Rewrite the baseline only to remove entries: `python -m backend.tests.test_aa1096_settings_lint` from the repo root.
