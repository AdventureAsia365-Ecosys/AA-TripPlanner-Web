<!--
Title: "<type>: <what changed>" in English. Types: feat, fix, chore, refactor, docs, ci, test, perf.
The "PR title" check enforces this. No "[AA-123]" in the title. In the body write "Refs AA-123";
never Fixes/Closes/Resolves (Linear would close the issue).
This repo is public: no account IDs, ARNs, SG/VPC IDs, API keys, secret values or traveller data in the PR
or the diff. Security and infrastructure findings go to the private ops docs, not here.
-->

## What

-

## Why

Refs AA-

## Risk and rollback

- Risk: low / medium / high (high: traveller data, guest tokens, edge key, migrations, deletes, Bedrock spend)
- Blast radius: frontend / browse Lambda / assembly Lambda / migration:
- Rollback: revert; redeploy the previous Lambda package or promote the previous Vercel deployment; or:

## Cost

- [ ] 0 USD: no new AWS resource, no new Bedrock or third-party API call path
- [ ] Adds spend: monthly estimate (Lambda, Bedrock calls per trip, map or geocoding API):

## Docs checklist

- [ ] `CONTEXT.md` updated (boundary, stack, ownership, endpoints or migrations list changed), or N/A
- [ ] ADR added or amended, and listed in `docs/adr/README.md`; cross-repo decisions go to the root ADRs; or N/A
- [ ] Runbook or how-to updated (smoke test, setup, an ops procedure), or N/A
- [ ] Contract or API shape change noted (contract version, CHANGELOG), or N/A
- [ ] Implementation note `docs/implementation-notes/AA-xxx.md`

## Verification

- [ ] CI green (Backend (pytest), Migrations (lint, PG 15 replay), Frontend (lint + build))
- [ ] Migration `tp-NNN`: identical copy in AA-CIS-Infra `db/migrations/tripplanner/`, applied on Dev by DB Migrate before this merges; or N/A
- [ ] Frontend change: Vercel preview checked on desktop and mobile; or N/A
- [ ] After deploy: the changed `/browse` or `/trip` route verified live (status + shape) through the API Gateway, not only the Lambda
- Evidence (commands, output, screenshots):
