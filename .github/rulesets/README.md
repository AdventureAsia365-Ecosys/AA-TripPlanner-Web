# Branch rulesets (AA-1119)

`main-merge-queue.json` is the merge queue ruleset of `main`, with the same parameters as AA-Booking's
`main: merge queue (AA-993)`: squash, groups of up to 5, ALLGREEN, 30-minute check timeout, and repository admins may
bypass only through a pull request. GitHub keeps the live ruleset; this file is the reviewed copy. The tech lead
applies it once with an admin token (the agents' GitHub App has no administration permission), from a checkout of
`main`:

    gh api -X POST repos/AdventureAsia365-Ecosys/AA-TripPlanner-Web/rulesets --input .github/rulesets/main-merge-queue.json

and turns on strict required checks in the classic branch protection (a PR must be up to date with `main`):

    gh api -X PATCH repos/AdventureAsia365-Ecosys/AA-TripPlanner-Web/branches/main/protection/required_status_checks -F strict=true

Order: only after every required check reports on `merge_group` (`ci.yml`, and the `gitleaks` job of
`secret-scan.yml`). Otherwise the queue waits for a check that never reports and drops the PR after the timeout.
`.github/scripts/test_merge_queue_ruleset.py` keeps the list of required checks and their workflows.

Read back:

    gh api repos/AdventureAsia365-Ecosys/AA-TripPlanner-Web/rulesets
    gh api repos/AdventureAsia365-Ecosys/AA-TripPlanner-Web/branches/main/protection/required_status_checks
