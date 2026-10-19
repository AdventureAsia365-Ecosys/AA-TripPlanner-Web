# AA-878 — TripPlanner: edge API key from Secrets Manager, fail closed (S243)

TripPlanner part (step T1) of AA-878, branch `fix/aa878-edge-key-from-secrets-manager`.
Companion changes:

- AA-CIS-Infra `fix/aa878-tp-lambda-secrets-arn-only` (step I2): removes `TRIPPLANNER_API_KEY`
  (plaintext, from `random_password.result`) and the dead `MAPBOX_GEOCODING_TOKEN_ARN` from the two
  Lambda env maps and grants `secretsmanager:GetSecretValue` on `tripplanner/dev/api-key`.
  **Merge and deploy this PR first**, then the Infra PR.
- Root `docs/aa878-secrets-register` (step B1): the secrets register.

## Decisions

- **Secret name derived from the existing `ENVIRONMENT` env** (`tripplanner/<ENVIRONMENT>/api-key`),
  not a new `*_ARN` env var: the plan forbids a new env var, and Terraform already sets
  `ENVIRONMENT` on both Lambdas. The name matches `aws_secretsmanager_secret.tripplanner_api_key`.
- **Lambda detection by `AWS_LAMBDA_FUNCTION_NAME`**, which the Lambda runtime always sets. No flag
  to forget.
- **The resolver lives in `shared/auth.py`**, not `config.py` as the plan sketched: `config.py` holds
  only env values and constants (the secret name template, cache TTL, retry window); a boto3 call
  there would run at import. Same split as `shared/db.py` (DSN by ARN).
- **Env value still wins.** During the rollout the Lambda env still holds the key, so this PR changes
  no live behaviour until Infra I2 removes it. Locally `TRIPPLANNER_API_KEY` keeps working as before.
- **Fail closed on Lambda**: missing `ENVIRONMENT`, read error, empty value or `REPLACE_ME` → 503
  `{"error": "api key unavailable"}`. An env value equal to `REPLACE_ME` is also refused.
- **Cache 10 min per container** (`API_KEY_CACHE_SECONDS = 600`): a rotation lands within 10 min
  without a redeploy. After a failed read, the last good key keeps working (it was valid moments
  ago; refusing every request on a Secrets Manager blip would be an outage), and the next read is
  tried after 10 s. With no good key yet, requests get 503 for those 10 s without calling Secrets
  Manager each time.
- **Log line** `{"evt": "api_key_unavailable", "reason": ...}` carries only the reason and the
  exception type, never the value.

## Changed

- `backend/config.py`: `ENVIRONMENT`, `API_KEY_SECRET_NAME_TEMPLATE`, `API_KEY_CACHE_SECONDS`,
  `API_KEY_RETRY_AFTER_SECONDS`. `TRIPPLANNER_API_KEY` stays (local/tests).
- `backend/shared/auth.py`: `expected_key()` + cache, `ApiKeyUnavailable`, `unavailable_response()`
  (503); `check_event()` signature unchanged, so both handlers need no change.
- `backend/tests/test_auth_secret.py`: 15 cases (env wins, local disabled, name derivation,
  401/503 paths, placeholder, stripping, cache TTL + rotation, last-good-key on refresh error,
  retry window). Existing `test_auth.py` unchanged and green.
- `CONTEXT.md`: "Edge shared secret" bullet in the backend section.

## Tradeoffs

- Up to 10 min where an old key still works after a rotation. Acceptable for a coarse "is this our
  BFF" gate; the Vercel value has to change at the same time anyway.
- Serving the last good key after a failed refresh is slightly less strict than "always re-read",
  but never opens the gate to a request without the right key.

## Should know

- **IAM before env removal**: today the Lambda role may read only the DB secret (and the Mapbox one).
  The secret path is reached only once Infra I2 removes `TRIPPLANNER_API_KEY` from the env, and I2
  adds the `GetSecretValue` grant on the api-key secret in the same apply. If the grant were missing,
  every request would get 503 (fail closed), not open.
- No new env var, no new secret. `boto3` is already a runtime dependency (packaged in the zip).
- Verification after I2 (main session): Lambda env keys without `TRIPPLANNER_API_KEY`; API GW
  `/browse/...` with no header or a wrong header → 401; TP site pages 200 through the BFF; no
  `api_key_unavailable` in CloudWatch.
