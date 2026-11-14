# AA-1096 lane l5-settings-env (S281) — no env var for a migrated setting (TripPlanner)

Last Done-when of AA-1096: "No env var remains for a migrated setting after one release". The 17 `tripplanner.*`
settings live in the CIS registry (CIS 414, read through `catalog.v_app_settings_v1`). Their fallback in
`backend/config.py` was "the env var when set, else the code default", and `TP_SETTINGS_ENV_FIRST=on` let a set env
var win over the view (the one-release emergency switch). The release has passed and AA-CIS-Infra lane L6 removed the
Lambda env vars, so the env reads go.

## Decisions

- The fallback of each setting is its code default, equal to the CIS seed (`test_s279_settings.py` parity tests
  unchanged). `TP_LEAD_HOLD_DAYS`, `TP_LEAD_TENANT_DAILY_CAP`, the seven `TP_RATE_*`, the two `TP_PLAN_CHAT_*` caps,
  `NARRATION_CACHE_TTL_HOURS`, `ADVISOR_HANDOFF_DEMO`, `REQUIRE_REGISTRATION_BEFORE_HANDOFF` are no longer read from the
  environment. `TP_DAILY_SPEND_CAP_USD` too: its value in force is the CIS app profile (`default_daily_budget_usd`, CIS
  414); the config value is only the fallback when the profile cannot be read.
- `TP_SETTINGS_ENV_FIRST` and the `Setting.env` field are removed; `resolve()` returns tenant | platform | fallback.
- `lead_hold_days()` and `demo_flag()` stay: the first clamps the value in force (`assembly/leads.py`), the second is
  still unit-tested as the parse rule; neither reads the environment now.
- Settings Lint (the job runs `test_s279_settings.py` too): `test_no_backend_module_reads_a_retired_env_var` fails on
  any env read (`_get`, `os.environ.get`, `os.getenv`, `os.environ[...]`) of a retired name in `backend/`, and
  `test_no_env_var_overrides_a_setting_any_more` proves a set env var (and the old switch) changes nothing.
- No migration here: the TripPlanner rows of `shared.setting_definition` never had an `env_override`; the AA-CIS-App
  lane's CIS 428 drops the "env var fallback" sentence from their descriptions and guides in the same wave.

## Changed

- `backend/config.py`, `backend/shared/settings.py`, `backend/.env.example` (the hand-off flag line), `CONTEXT.md`,
  `backend/tests/test_s279_settings.py`.

## Tradeoffs

- The daily cap's fallback keeps its source label `env` in `llm_gateway.budget_status` / the jobs `settings` op (an
  API value tests and the CIS page read); it now means "the code fallback". Renaming the label is a contract change,
  left for a follow-up.

## Should know

- Other env-only TripPlanner thresholds are not migrated settings and stay (out of scope): the per-stage daily caps
  `TP_DAILY_SPEND_CAP_SEARCH_USD` / `_COMPOSE_USD`, the lead webhook timeouts, `TP_LEAD_CRM_PULL_FRESH_S`.
