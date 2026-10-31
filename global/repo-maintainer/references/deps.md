# deps: Dependabot PRs

`context.json` lists, in `prs`, the bot PRs that changed since a run last handled them and whose checks have finished (or that have no checks). Each entry has the fields described in `prs.md`, plus these, which the runner checked from GitHub's data rather than the PR's text:

- `update_type`: `patch`, `minor`, `major` or `unknown`. It's the largest bump in the PR, taken from Dependabot's commit metadata. Treat `unknown` as `major`.
- `verified`: true when every commit is the bot's and has a verified signature, the branch is in this repo, and the PR changes only dependency manifests, lockfiles, container files and workflow files
- `problems`: why `verified` is false
- `files`: the changed files
- `merge_allowed_by_policy`: the update type is in `policy.merge`

`policy` also has `merge_method`, `rebase_conflicts` and `local_checks_without_ci`.

Work oldest first. After each merge, the remaining PRs may conflict or go out of date. Dependabot rebases them by itself, and the next run picks them up.

## Merge when all of these hold

1. `verified` is true. If not, don't merge it or check it out: someone else changed it, or it isn't what it claims to be. List it under Needs attention with its `problems`.
2. `merge_allowed_by_policy` is true. If not, list it under Needs attention as waiting for the owner.
3. `conflicting` is false.
4. The checks passed:
   - `success`: passed.
   - `failure`: don't merge. See "Failing checks" below.
   - `none`: the repo runs no CI on this PR. If `policy.local_checks_without_ci` is true, fetch it with `git fetch <remote> pull/<n>/head`, and check that `git rev-parse FETCH_HEAD` equals `head` from the context. If it doesn't, someone pushed after the runner verified the PR: run nothing, and leave the PR for the next run to verify again. Otherwise check out exactly that commit, without creating a branch (`git switch --detach <head>`), install dependencies the way the repo does, and run `checks`. Then `git restore` any tracked files the install changed. If they pass, the checks count as passed. If you can't work out how to verify the change, don't merge it. List it under Needs attention.
5. **Major and unknown updates only:** the release notes show nothing that breaks this repo. Find the notes for every version between old and new: the links in the PR body, the dependency's GitHub releases, or its CHANGELOG. List each breaking change, then search the checkout for the affected APIs, options or config (`git grep`). Merge only if none of them is used here, or if the repo uses them and the passing checks clearly exercise that code. Otherwise list it under Needs attention, naming the breaking change and where the repo uses it.

For patch and minor updates, skim the release notes only for a core dependency, such as a framework, runtime or build tool, and look for known regressions.

Merge with:

```sh
gh pr merge <n> -R <repo> --<merge_method> --match-head-commit <head>
```

`--match-head-commit` makes GitHub refuse the merge if anyone pushed after the runner verified the PR. Never add `--admin`. If GitHub refuses the merge:

- **Approval required:** list it under Needs attention as "verified, checks green, needs your approval".
- **Branch out of date:** comment `@dependabot rebase` (see below).
- **Head changed:** leave it; the next run checks it again.

## Conflicting PRs

If `policy.rebase_conflicts` is true, comment exactly `@dependabot rebase` with nothing else, so Dependabot recognizes the command: `gh pr comment <n> -R <repo> --body "@dependabot rebase"`. First check that you haven't already asked since the last push. If you have, list the PR under Needs attention instead.

## Failing checks

Find out why: `gh pr checks <n> -R <repo>`, then `gh run view <run-id> -R <repo> --log-failed`.

- **Flaky or unrelated** (the same check fails on `branch`, or the failure has nothing to do with the dependency): rerun it once with `gh run rerun <run-id> -R <repo> --failed`, and note it.
- **Caused by the update:** read the release notes for the breaking change. If the fix in this repo's code is small and clear, fetch the PR and check that `FETCH_HEAD` equals `head`, as above. Then make a branch from that verified commit (`git switch -c <branch_prefix>deps-<name> <head>`), fix the code, run `checks`, push, and open a PR against `branch` titled after the bump that says it supersedes #<n>. Leave Dependabot's PR for the owner to close. Otherwise, list the diagnosis under Needs attention.

## Summary

- **Done:** each merge (number, title, update type), each `@dependabot rebase`, rerun, and fix PR.
- **Needs attention:** unverified PRs and their problems, PRs waiting for the owner, breaking changes, and failures you couldn't fix.
- **Notes:** security updates (the PR body cites a GHSA or CVE) and anything you left for the next run.
