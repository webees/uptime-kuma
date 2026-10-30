# Target Mode

The round protocol lives here instead of in a chat message, so a new round needs
no prompt to be carried over by hand. `docs/recurring_issues.md` holds the
invariants; this file holds the procedure that keeps them honest.

## Role

One executor works this repository alone. `webees@vaultwarden` is a read-only
reference for shape, never a source of truth about this project. Facts come from
the worktree, the workflows and the live application, not from an earlier
summary.

## Hard rules

- Answer in Chinese; commit messages, PR titles and PR bodies in English.
- Branch as `codex/<slug>`; never commit to `main` directly.
- Create and edit files with `apply_patch` only. Shell may read, build, test and
  run `git`.
- Never `git reset --hard`. Leave unrelated files alone.
- Tests run through `python3.13 -m unittest`. There is no `pytest`.
- A green `validate` is not a deployment. After a merge, `deploy` is proven by
  the verification step and by re-reading `flyctl releases`/`status` with the
  same `jq` filters CI uses.
- Only output that was actually produced may be written under `实测结果`.
- Merging to `main`, `gh secret set` and `fly secrets set` are announced in one
  sentence with their scope before they run.
- One reviewable slice per commit, pushed immediately with a PR. Do not batch a
  round into a single opaque commit.

## Round entry gate

Run `python3.13 scripts/round_open.py` first. It prints the matrix skeleton for
every recorded class plus the open residuals, so neither can be forgotten.

Then, before any other work:

1. Read `docs/recurring_issues.md` and `tests/test_recurring_gate.py` whole.
2. For each class, confirm its authoritative guard still exists, is still the
   only one, and still covers every equivalent path. Trace history with
   `git log -S` and `git log -G`, and classify each class as never fixed,
   regressed after simplification, or partially fixed.
3. Fill the matrix with real command output.
4. Repair a failing, unverified or weakened row in the round's first patch,
   alone, before anything else.

Nothing may be optimized, scored, merged, deployed or called finished while a
row is missing, failing, unverified, or carries an unclassified residual.

## Matrix

One row per class, always these eight columns, in this order:

| Class | History | Authoritative guard | Positive | Boundary | Negative | Mutation control | Observed result |
|---|---|---|---|---|---|---|---|

- `History` is the `git log -S` / `git log -G` classification, not a guess.
- `Authoritative guard` is one `path:symbol` pair. Two guards for one invariant
  means the invariant is still fragmented.
- `Positive`, `Boundary` and `Negative` each name the command or test that was
  run, and the row is only filled once that ran.
- `Mutation control` names the control and states what changed when the guard
  was removed. A control that stays green proves nothing.
- `Observed result` quotes the actual output. Anything not run says `未验证`
  with the reason.

The matrix goes into the PR body and the final report. It is a review artifact,
not a score, and it never becomes a per-round file.

## Report order

Conclusion, then matrix, then changes, then verification with run URLs and the
raw `flyctl`/`curl` output, then residuals. No preamble.

## Rejection criteria

A round is void and starts over when any of these is true:

- a matrix column or row is missing;
- only the positive path was demonstrated and no control turned red;
- an earlier run's output is presented as this round's evidence;
- deployment is claimed without the token or without the post-deploy reads;
- a green `validate` is reported as a successful deployment;
- a business file was written through the shell;
- the residual list grew without the trigger and the observation being written
  down.

## Residual list

`README.md` under `## ⚠️ Residual risk` is the only place a residual lives. A
residual is opened there in the same PR that discovers it, with its trigger and
how it would be observed, and closed there when the fix lands. The list is
allowed to shrink only.
