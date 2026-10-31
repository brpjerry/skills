# sync: keep a fork current

## The fork model

The owner keeps forks this way. It applies to every task in a fork.

- **Upstream** is the project the fork came from. Nothing is ever pushed there.
- **The mirror** (`mirror`, often `master` or `main`) is a clean copy of upstream's branch. It has no commits of the fork's own, so refreshing it is a fast-forward, which the runner does before starting you. Never commit to it.
- **Feature branches** carry the fork's changes: one concern each, or a group of related ones, each cut from the mirror. They're kept current by merging the mirror into them, which preserves their history and earlier conflict resolutions. Never rebase, squash, amend or force-push them. New, independent work gets a new branch from the mirror; follow-up work goes on the branch that owns the concern.
- **The integration branch** (`integration`, such as `features-master`), when the fork has one, merges every feature branch. It's what gets built and deployed. It only ever receives merges of feature branches. A change it needs goes on the feature branch that owns the concern, which is then merged in again.
- **Skipped branches** (`skip`, such as `bench/*` for temporary instrumentation) are never refreshed and never merged into the integration branch.
- **Retired branches** are kept as annotated `archive/<name>` tags, pushed before the branch is deleted. Retiring is the owner's decision. You only propose it.
- **A fork without a mirror** merges upstream straight into its feature branches, which is often just the default branch.

## Context

`context.json` has `upstream` and `upstream_ref` (see SKILL.md), plus:

- `upstream_branch`, `upstream_head`
- `mirror`: the mirror branch, or null. `runner_did` says whether the runner fast-forwarded it.
- `target`: what to merge into feature branches. It's `<remote>/<mirror>` (already fast-forwarded), or `upstream_ref` without a mirror.
- `merge_message`: the message for those merges, with `{branch}` standing for the feature branch. It matches the owner's history, such as `Merge branch 'master' into flac-fixes`.
- `features`: the feature branches. Unless the config lists them, these are all branches with commits upstream lacks, minus the mirror, the integration branch, `skip` and your own. Each has:
  - `name`, and `ref` (`<remote>/<name>`)
  - `behind`: upstream commits it lacks
  - `own_commits`
  - `needs_refresh`
  - `integrated`: whether it's already merged into the integration branch
  - `worktree`: where the owner has it checked out, or null
- `integration`, `skip`, and `review_prefix` (for branches the owner should look at)

## 1. Refresh feature branches

For each feature branch with `needs_refresh`:

1. `git switch --detach <ref>`
2. `git merge --no-ff -m "<merge_message>" <target>`, with `{branch}` replaced by the branch name.
3. **Conflicts.** If the repo has `rerere.enabled`, git may replay a resolution from an earlier refresh. Check each file it resolved (`git rerere status`, `git diff`) instead of trusting it. Resolve each conflict so the branch's own change sits on top of upstream's. `git log --oneline <target> -- <file>` and `git log --oneline <target>..<ref> -- <file>` show what each side did and why. If a conflict needs a judgment you can't make confidently, run `git merge --abort`, list the branch and files under Needs attention, and move on to the next branch.
4. Keep the result without pushing it yet: `git update-ref refs/repo-maintainer/refresh/<name> HEAD`.

Merges whose conflicts you resolved get pushed like the rest once checks pass. Record each resolution in the summary: the branch, the files, and how you resolved them, so the owner can review them after the fact.

## 2. Validate and publish

Validate the way the owner does. With an integration branch, the reintegrated result is what gets built, so the checks run once, on it. Without one, each refreshed branch is checked on its own.

### With an integration branch

1. `git switch --detach <remote>/<integration>`
2. Merge every feature branch outside `skip`, in the order of `features`, with `git merge --no-ff -m "Merge branch '<name>' into <integration>" <commit>`. Use `refs/repo-maintainer/refresh/<name>` for a branch you refreshed. Use `<remote>/<name>` for a branch that wasn't refreshed and isn't `integrated` yet. Skip branches whose refresh you abandoned in step 1. Feature branches can conflict with each other; resolve those as in step 1, where rerere often has the resolution from the last integration.
3. Run `checks`. If they fail:
   - Find the feature branch whose change causes it, for example by building its refreshed commit on its own.
   - Fix it on that branch: `git switch --detach refs/repo-maintainer/refresh/<name>`, commit the fix, and update the ref. Then redo this section from step 1. Never commit a fix straight onto the integration branch.
   - If you can't fix it, push nothing to the owner's branches. Push the integration attempt to `<review_prefix><integration>`, open a PR into `<integration>` explaining the failure, and list it under Needs attention.
4. Publish everything in one push, so the fork never shows refreshed feature branches without their integration. Every update must be a fast-forward; never force.

   ```sh
   git push --atomic <remote> refs/repo-maintainer/refresh/<a>:refs/heads/<a> refs/repo-maintainer/refresh/<b>:refs/heads/<b> HEAD:refs/heads/<integration>
   ```

   If it's rejected because something moved on the remote, run `git fetch <remote>` and start over from section 1 once. If it's rejected again, report it.
5. Check that every feature branch outside `skip` is an ancestor of the integration branch. List any that aren't under Needs attention.

### Without an integration branch

For each refreshed branch:

- Run `checks` on `refs/repo-maintainer/refresh/<name>`.
- If they pass, push with `git push <remote> refs/repo-maintainer/refresh/<name>:refs/heads/<name>`.
- If they fail because of the merge, and the fix is small and belongs to this branch's concern, commit it on top, update the ref, and rerun.
- Otherwise, push the merge to `<review_prefix><name>`, open a PR into `<name>` explaining the failure, and list it under Needs attention.

### Afterwards

Delete your temporary refs with `git update-ref -d refs/repo-maintainer/refresh/<name>`.

## 3. Look for redundant branches

Upstream sometimes lands a fork's change, or fixes the same problem another way. For each feature branch, run `git cherry <target> <remote>/<name>`; lines starting with `-` are commits upstream already has. If all of a branch's commits are upstream, or upstream clearly fixed the same thing, propose retiring the branch under Needs attention. Give the reason and the owner's procedure, but don't run it:

```sh
git tag -a archive/<name> <remote>/<name> -m "<why it was retired>"
git push <remote> archive/<name>
git push <remote> --delete <name>
```

If the branch has a `worktree`, say so; the owner moves it aside when retiring the branch.

## Never

- Push to upstream, or commit to the mirror.
- Rebase, squash, amend or force-push a fork branch, or commit straight to the integration branch.
- Create or delete tags, or delete branches.
- Refresh or integrate a `skip` branch.
- Touch the owner's local branches or worktrees. The runner fast-forwards the clean ones after you finish.

## Summary

- **Done:** each branch refreshed (name, upstream commits merged, conflicts resolved and how), the integration merges, the checks you ran, and what you pushed.
- **Needs attention:** unresolved conflicts, failing checks and their review branches, branches missing from the integration branch, and retirement proposals.
- **Notes:** resolutions rerere supplied, and anything left for the next run.
