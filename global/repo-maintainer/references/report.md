# report: the daily report

The runner writes quiet days' reports itself. You run when the day had activity. `context.json` has:

- `date`: the day to report on
- `runs`: that day's runs that did something, each with `time`, `task`, `status` (`ok`, `done`, `failed` or `timeout`), `note`, `summary` (the path of that run's summary, or null), `run_dir`, and `denied` (actions auto mode refused). A `done` run was handled by the runner without an agent, such as a fork fast-forward, and its `note` says what happened.
- `skipped`: how many runs per task found nothing to do
- `blocked`: runs that couldn't start, with `task`, `reason` and `count`. Reasons include the checkout having uncommitted changes or a merge in progress, being offline, and backing off after failures.
- `open_prs`: on GitHub, your own and the dependency bot's PRs that are still open, with `days_open` and `days_idle`. Null elsewhere, or if GitHub couldn't be reached.
- `stale_days`: PRs idle at least this long count as stale
- `paused`: tasks backing off after repeated failures

## Steps

1. Read every summary in `runs`.
2. Check where things stand now. For each PR the summaries say you opened or left for the owner, look up its state, so the report doesn't list a PR as waiting when it has since been merged. Use `gh pr view <n> -R <repo> --json state,mergedAt` on GitHub, or `fj pr view <repo>#<n>` on Forgejo. With plain git, check whether its branch is merged: `git merge-base --is-ancestor <remote>/<branch> <remote>/<base>`.
3. Write `output` in the format below. The runner appends a table of the day's runs, so don't add one.

Use only the summaries, the context and your lookups. Don't invent activity, and don't repeat the same item in two sections. Link PRs by their URLs on the forge. The report is read from another folder, so give local files as absolute paths in backticks, never as relative links.

```markdown
# <repo>: <date>

<One line: what happened and what needs the owner, e.g. "Merged 3 Dependabot PRs; #42 needs your review.">

## Needs your attention

1. **<short title>:** what it is, why it needs a person, and what to do. <link>

## Done

- **Merged:** ...
- **Opened:** ...
- **Updated:** ...
- **Synced:** ...

## Notes

- ...
```

**Needs your attention** goes most urgent first. It draws from these sources:

- items under Needs attention in the summaries, unless your lookup shows they're resolved
- `failed` and `timeout` runs (put the note in plain words)
- tasks in `paused`
- `blocked` runs whose reason is about the checkout (uncommitted changes, a merge in progress), since the owner has to clear it before work resumes
- runs with `denied` above zero (say which task, and that `<run_dir>/denied.json` lists the refused actions)
- PRs in `open_prs` with `days_idle` of at least `stale_days`, as one grouped item

Under **Done**, leave out groups with nothing in them. Write "None." under a section that's empty.
