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

Run `python3.13 scripts/round_open.py` first. It resolves the audit baseline,
prints how many commits still lie past it, the matrix skeleton for every
recorded class and the open residuals, so none of the three can be forgotten.

The baseline is a commit that must resolve, not a written count: the commit
that records a count makes that count wrong. A missing baseline exits `1`, an
unresolvable one exits `2`, so a typo cannot read as "nothing owed".

The count is reported twice on purpose, because the two numbers answer
different questions:

- `to audit this round` is everything past the baseline up to `HEAD`. Inside a
  pull request it is never zero — the round's own commits are exactly that
  distance, and the matrix is their evidence.
- `which this round cannot cover` is the same distance measured to the branch
  the PR lands on. Those commits are already on `main` with no matrix behind
  them. This is the number that must be `0`.

`validate` therefore runs `--require-closed --entry "$BASE"` inside the
`🧾 Round matrix` step, where `BASE` is the pull request's base commit. A round
that opens on a stale baseline fails its own PR instead of merging the debt
forward. Only the PR path is gated: a `push` build has no base, and the
`workflow_call` copy would otherwise fail the publish for the same reason.

An `Observed result` of `未验证` with a reason is legal, but not for a class
whose authoritative guard this PR edits. The step passes the changed file list
from the same API to `pr_matrix.py --touched`, and any class naming one of
those files as its authority must carry a real observation. The reader has the
diff in hand; no later round will look again.

When a round extends the baseline, it names the baseline it supersedes in the
same sentence and the script requires the new one to descend from it. A hash is
cheap; pasting a newer one closes the gap as well as auditing a commit does, so
the chain is what proves the middle was reached. A missing predecessor exits
`4`, a predecessor that does not resolve or is not an ancestor exits `2`.
Never restart or fork the chain to make the gate green.

Naming a predecessor is still an act of self-certification while both hashes
come from the same edit. So on the PR path the named predecessor must also be
the baseline recorded in `docs/recurring_issues.md` **at the base commit**, and
a base that records no baseline exits `5`. Copy the value out of
`git show <base>:docs/recurring_issues.md` instead of out of the working file.

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

- `History` is the `git log -S` / `git log -G` classification plus the commit
  it rests on — `unchanged, git log -Sx → abc1234`, `regressed by abcdef1`.
  A bare `n/a` is not a classification and `validate` rejects it, as does a hash
  that `git cat-file` cannot resolve or that resolves but is not reachable from
  `HEAD`: a squash merge drops the branch commit, so such a row only passes on
  the clone that still holds the branch.
  The command is run, not read: every cited hash has to be in its output (`-S`
  and `-G` are re-executed with the needle as written), and a History with no
  `git log` command is rejected, because any reachable commit otherwise counts
  as history for any class. The needle is taken whole — path separators,
  quotes and spaces included — because re-running a truncated prefix searches
  a wider set than the row claims, so a hash that search never reported would
  read as its output.
- `Authoritative guard` is one `path:symbol` pair and has to be one the class
  records under `Authority:`; `validate` rejects another path, a neighbour's
  symbol on a shared file, and a symbol that no longer occurs in that file. Two
  guards for one invariant means it is still fragmented.
- `Positive`, `Boundary` and `Negative` each name the command or test that was
  run, and the row is only filled once that ran. A backticked `path:symbol`
  must still resolve here too: "ran", "yes" and "ok" describe a run without
  letting a reader repeat it, and a test that was since deleted repeats nothing.
  `validate` rejects a cell that names no run at all, and a span whose content
  is only a word: backticks are not evidence, so `` `yes` `` fails where
  `` `python3.13 -m unittest x` `` passes.
- `Mutation control` names the runnable test or command in backticks and states
  what changed when the guard was removed. "5 controls" is a count, not a
  location; a control that stays green proves nothing. A backticked
  `path:symbol` must still exist: a control in a deleted file or a deleted case
  cannot be run. A guard file is not runnable by itself — `config/Caddyfile`
  is directives, so a control pointing there has to name the test that drives
  them.
  The control and the three evidence columns name the class's *own* control —
  one of the `path:symbol` runs it records under `Mutation control:`. A test
  that resolves is not necessarily this test, so a neighbour's row copied
  across reads as an audit of an invariant it never touches.
- `Observed result` quotes the actual output. Anything not run says `未验证`
  with the reason; anything else is a claim and must quote, so `ok` alone does
  not pass — and a quoted `ok` does not either, because a quote with no number
  or verdict in it is still a mood.

The matrix goes into the PR body and the final report. It is a review artifact,
not a score, and it never becomes a per-round file.

The PR body is machine-checked: `scripts/pr_matrix.py` runs in `validate` for
pull requests and rejects a missing class, a missing or extra column, an empty
cell, a duplicate row, and a bare `未验证` with no reason. A `## Matrix` section
that exists is read from the GitHub API, not from a file the branch carries, so
the table in the body is the table that was checked.

## Report order

Conclusion, then matrix, then changes, then verification with run URLs and the
raw `flyctl`/`curl` output, then residuals. No preamble.

Those are five headings — `## Conclusion`, `## Matrix`, `## Changes`,
`## Verification`, `## Residual` — because a body can satisfy every per-cell
rule and still be a table alone. `scripts/pr_matrix.py` rejects a missing one,
the same set arriving in another order, a heading written twice, and any of the
five left with no content. A heading is a promise to write under it: the empty
`## Verification` is what the template leaves behind, and an empty conclusion or
residual is the same shape one section over.

A heading inside a fence is a quotation, not a section. The examples a body
pastes in — a skeleton to fix, a table to restore — are ignored, so the sections
and the table rows are the ones outside code blocks.

`## Verification` is the section a reader repeats, so every bullet in it names
a command in backticks or links a run. "passed", "see above" and "all green"
describe a verification without letting anyone re-run or open it.

The command has to still run: a cited script or test file that is gone, and a
case name that no longer occurs in its file, are reported. A test moved in the
same PR therefore has to have its verification lines moved with it.
An example that is *meant* to be absent is marked `absent:` on its line, or the
check reports the negative case it is demonstrating.

`## Changes` is the list a reviewer maps onto the diff, so every bullet names a
file and the file has to exist. With `--touched`, every changed file also has
to appear somewhere in the section; "made things better" costs the reader the
same work as a bullet for a file the PR never touched.

`No preamble` is the first sentence of the order, and it is the only part of it
that a reader can break without breaking the structure: text above
`## Conclusion` parses cleanly and still makes the reader read the conclusion
twice. Any non-blank text before the first `##` heading is reported. A leading
blank line is not a preamble, because the template starts that way.

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
