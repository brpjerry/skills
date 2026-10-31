# scan: find and fix inefficiencies

You start on a detached HEAD at `scan_ref`. `context.json` has:

- `scan_ref`: what you're scanning: `base_ref`, or a fork's integration branch. `head` is its commit. `last_scan_head` is the commit the last scan saw, or null on the first scan.
- `changes_since_last_scan`: `git diff --stat` between them
- `fork_scope`: for a fork, `base` (the mirror or upstream) and `head`. The fork's own code is what `git diff <base>...<head>` shows. Null for a repo that isn't a fork.
- `open`: your improvement PRs still open on GitHub. Elsewhere, your improvement branches not yet merged.
- `declined_prs`: on GitHub, your improvement PRs closed without merging. The owner said no to these, so don't propose them again. Elsewhere, empty; check the backlog's statuses instead.
- `merged_prs`: on GitHub, your improvement PRs that were merged
- `backlog`: a findings file kept between scans, outside the checkout; it may not exist yet
- `mode`: `pr` (fix and open PRs) or `report` (record findings only)
- `max_new_prs`: how many PRs you may open this run
- `branch_prefix_scan`: the prefix for your branches
- `focus`: areas to favor; empty means everything
- `exclude`: paths to leave alone

## 1. Survey

Read the backlog first, if it exists. In a fork, survey only the fork's own code (`fork_scope`). Upstream's code is upstream's to improve. On the first scan, survey the whole repo, starting with its most central code: entry points, hot loops, request handlers, build and CI. After that, look mainly at `changes_since_last_scan`, and recheck the open backlog items. Skip vendored, generated and `exclude` paths. In a large repo, you may start subagents on `subagent_model` to survey separate areas. Give each one this list and the evidence rules below.

What counts as an inefficiency:

- **Runtime:** quadratic work on growing input, repeated work inside loops, N+1 queries or requests, unbounded reads into memory, blocking I/O on async paths, needless copies, and missing caching of expensive pure results.
- **Code:** duplicated logic, dead code (unreferenced functions, unused dependencies, stale flags), and hand-rolled versions of something the standard library or an existing helper already does.
- **Build and CI:** missing dependency caches, redundant or serial jobs that could run in parallel, needless full rebuilds, slow or outdated actions, and bloated container layers.
- **Dependencies:** unused dependencies, a heavy dependency pulled in for one small function, and two dependencies doing the same job.

Every finding needs evidence: file:line, why it's wasteful, its rough impact, and how risky the fix is. Measure impact when you can by running the repo's own tests, benchmarks or build in the checkout, otherwise reason it out. Don't run the repo's tools against the machine itself: package managers, system services, hardware tools. Style preferences and speculative micro-optimizations aren't findings.

## 2. Pick

Rank findings by impact times confidence, divided by risk. A good candidate for a PR:

- is a clear win, preserves behavior, and is small enough to review in one sitting (roughly under 200 changed lines)
- is covered by existing tests, or easy to cover
- isn't in `declined_prs`, isn't marked declined in the backlog, and doesn't overlap `open`

Large refactors go in the backlog as proposals, not PRs.

## 3. Fix (mode `pr`)

Open up to `max_new_prs` PRs, each with one finding:

1. Start a branch: `git switch -c <branch_prefix_scan><short-name> <base_ref>`. In a fork, start it from the feature branch that owns the code (`<remote>/<feature>`) and open the PR into that branch, never into the mirror or the integration branch. `git log --oneline <fork_scope.base>..<remote>/<feature> -- <file>` shows which feature branch touched the file.
2. Make the change in the repo's style. Add a test if the behavior you preserved isn't covered.
3. Run `checks`. For a performance claim, measure before and after with a benchmark, a timing, or CI duration, and keep the numbers.
4. Commit, push, and open a PR into the branch you started from (see "Forges" in SKILL.md). In the body, say what was wasteful (with file:line), what you changed, the evidence and numbers, how you verified it, and any risk. End it with the marker.

If checks fail and you can't fix the failure confidently, don't open the PR. Record the finding in the backlog with what went wrong.

In mode `report`, change no code; the findings go to the backlog and the summary.

## 4. Update the backlog

Rewrite `backlog` as a ranked list. For each finding give a short title, file:line, impact, fix risk, and status: open, PR #n (or its branch), or declined. Drop findings that were fixed, merged or no longer apply. Keep at most 50, dropping the weakest.

## Summary

- **Done:** PRs opened, with a one-line evidence summary each.
- **Needs attention:** findings big enough for the owner to decide on, such as large refactors or dependency swaps.
- **Notes:** how many findings are in the backlog, and the top three.
