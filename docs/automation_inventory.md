# Automation Inventory

Audit baseline: `8e00072` on 2026-11-02. This inventory answers whether the
workflows and scripts are reachable, what each protects, and whether any can be
removed. A script is a deletion candidate only after proving it has no caller,
no runtime or release dependency, and no rollback value.

## Workflows

| File | Triggers | Responsibility | Production effect | Decision |
| :--- | :--- | :--- | :--- | :--- |
| `validate.yml` | push to `main`, pull request, manual, reusable call | shell/Python checks, actionlint, round matrix, image smoke and Caddy validation | required merge gate; `ghcr.yaml` calls it before publishing | retain; a required status context |
| `ghcr.yaml` | push to `main` or tags, weekly schedule, manual | validate, multi-arch build, digest smoke, SBOM, Trivy gates, exact-digest deploy, Fly verification, `latest` promotion | sole trusted publish and production deploy path | retain; cannot be merged into a local helper |
| `deploy-drift.yml` | after each `ghcr.yaml` run, daily schedule, manual | compares the running machine with the promoted `latest` amd64 child | detects hand deploys and incomplete promotions after the fact | retain; independent observer of the publish path |
| `upstream-updates.yml` | daily schedule, manual | resolves Uptime Kuma/supercronic and direct Node.js pins, opens or refreshes `deps/upstream`, waits for `validate`, merges stable updates, dispatches publish | keeps the upstream image, scheduler and direct runtime dependencies current | retain; separate from runtime scripts because it needs write/merge permissions |
| `dependabot-automerge.yml` | Dependabot pull request events | accepts only same-Action immutable SHA/comment updates, waits for required `validate`, merges | keeps GitHub Action pins current without accepting workflow logic changes | retain; `pull_request_target` security boundary |
| `ghcr-cleanup.yml` | daily schedule, push to `main`, manual | removes untagged or stale GHCR images and shares the publish registry lock | bounds registry growth without deleting a build currently under test | retain; cleanup has different permissions and failure behavior |
| `automation-health.yml` | daily schedule, watched workflow completions, manual | schedule watchdog, failed-run visibility, credential expiry/webhook preflight | fail-closes deployment prerequisites and keeps disabled schedules visible | retain; it is one half of the mutual watchdog pair |

There is no deletion candidate among the seven workflows. Merging publish and
cleanup would couple packages-write deletion to deploy concurrency; merging the
two watchdogs would remove the independent absence detector. The remaining
workflows have distinct event and permission boundaries.

## Scripts

| File | Main caller | Responsibility | Decision |
| :--- | :--- | :--- | :--- |
| `entrypoint.sh` | container entrypoint | validates startup inputs and hands control to the process supervisors | retain; runtime-critical |
| `entrypoint/config.sh` | `entrypoint.sh`, image tests | DNS/URL/SMTP input validation and Caddy address audit | retain; runtime-critical |
| `services.sh` | container entrypoint | supervises Caddy and supercronic; application exit stops the container | retain; runtime-critical |
| `restic.sh` | container cron and SSH operations | consistent SQLite backup, freshness check, isolated restore drill, confirmed in-place restore | retain; data protection and rollback |
| `rebuild.sh` | `ghcr.yaml` scheduled probe | counts pending published-image upgrades so weekly builds skip when current | retain; avoids unnecessary production deploys |
| `deploy_drift.py` | `deploy-drift.yml` | parses release, machine and `latest` manifests and fails on unreadable or mismatched state | retain; independent production audit |
| `upstream_deps.py` | `upstream-updates.yml` | resolves, compares and rewrites the five image/tool pins plus the explicit Node.js and tar override pins | retain; bounded dependency update boundary |
| `check_schedules.py` | both watchdog workflows | detects disabled or overdue scheduled workflows through GitHub metadata | retain; watchdog evidence |
| `check_automation_credentials.py` | `automation-health.yml` | fail-closed token lifetime and HTTPS webhook preflight without secret output | retain; deploy prerequisite |
| `validate_action_pin_update.py` | `dependabot-automerge.yml` | API-diff parser that accepts only complete same-Action pin replacements | retain; auto-merge security boundary |
| `pr_matrix.py` | `validate.yml` | parses the required nine-class PR evidence matrix and touched-file coverage | retain; merge gate |
| `round_open.py` | `validate.yml`, maintainers | prints the round skeleton and enforces audit-baseline ancestry | retain; merge gate and evidence generator |
| `branch_rule.py` | maintainers, tests | compares the live `main` protection rule with the recorded rule | retain; external-rule drift check |

The scripts are not one undifferentiated CI layer. Four are shipped runtime
files, several are testable CI security or deployment gates, and the remaining
ones generate or audit evidence. No merge or delete action is justified without
first moving the corresponding tests and rollback evidence.

## Update Lanes

| Dependency lane | Cadence | Current control | Remaining gap |
| :--- | :--- | :--- | :--- |
| GitHub Actions pins | daily | Dependabot grouped pull request, then `dependabot-automerge.yml` after `validate` | none observed |
| Uptime Kuma image, supercronic and direct Node.js packages | daily | `upstream-updates.yml` plus `upstream_deps.py`; tar overrides follow the direct `tar` pin; npm pins ahead of the registry's `latest` are retained | stable release merge requires a green branch `validate`; major npm updates use the same full image/runtime gate |
| Base OS packages | weekly | `rebuild.sh` probe followed by build, scan, deploy and verify | unfixed Debian advisories remain report-only by design |
| Direct Node runtime packages and tar overrides | daily | `upstream-updates.yml` resolves the explicit package list, keeps all four tar overrides equal to the direct `tar` pin, and refuses missing or unexpected values | no separate lane required; image tests still cover the installed tree |

## Notification And Credential Boundary

`ALERT_WEBHOOK_URL` is an external prerequisite. The repository currently has
no configured value, so a real external alert cannot be claimed. Automation
Health's scheduled and manual preflight treats that absence as a failure; the
individual workflows still annotate the skipped delivery instead of printing a
secret or claiming success.
