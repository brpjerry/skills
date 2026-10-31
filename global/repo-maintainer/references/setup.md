# setup: start maintaining a repo

The user started you inside their checkout of the repo to maintain. You're working with them, so ask when a choice is theirs. Below, `maintainer.py` means `python3 <this skill's directory>/scripts/maintainer.py`.

1. **Prerequisites.** Check these and report anything missing:
   - The forge CLI is logged in. For GitHub, `gh auth status` should show the `repo` and `workflow` scopes. For Forgejo, `fj auth list` should show the server. Plain git needs nothing extra.
   - The agent CLI the user wants (`claude` or `codex`) is installed and logged in.
   - `loginctl show-user "$USER" -p Linger`: with `Linger=no`, timers run only while the user is logged in. That's fine on a laptop. On a server, suggest the user run `loginctl enable-linger`.
2. **Explain the checkout rules.** Make sure the user is fine with these; they suit a checkout the user doesn't edit in while timers are running.
   - Timer runs work directly in this checkout.
   - A run doesn't start while the checkout has uncommitted changes to tracked files, or a merge or rebase in progress. It's recorded as blocked, and the daily report says so.
   - During a run, the checkout sits on a detached HEAD. Afterwards the runner switches back to whatever was checked out, and stashes anything the agent left uncommitted.
   - The agent's own branches all start with `maintainer/`.
3. **Add the repo.** Run `maintainer.py add` from the checkout, adding `--agent codex` for Codex. It uses the `origin` remote unless given `--remote`, and works out:
   - **The forge:** GitHub; Forgejo, when an `fj` login points at the remote's server; otherwise plain git.
   - **Whether it's a fork:** from GitHub's parent, a remote named `upstream`, or `--upstream <url>`.
   - **For a fork:** the mirror branch, a likely integration branch, and the feature branches.

   It writes `~/.config/repo-maintainer/<slug>.toml`. If it warns that the remote didn't answer without an SSH agent or a prompt, pass that on: timer runs have neither, so the remote needs an HTTPS URL with a credential helper, or a key without a passphrase.
4. **Forks.** Read "The fork model" in `sync.md` and walk the user through it with their branches. Confirm each of these and write any change to the config's `[sync]` table:
   - `mirror`: the branch that mirrors upstream, if any. It must have no commits of its own.
   - `integration`: the branch that merges every feature branch, if any. `add` only guesses this.
   - The feature list `add` printed. Remove branches the user doesn't maintain, either by matching them with a `skip` pattern, or by listing the maintained ones in `features`.
   - `skip`: `bench/*` by default.
   - `update_local`: whether to fast-forward their clean local branches and worktrees after a refresh.
   - `sync.timeout_minutes` and `sync.budget_usd`: a refresh merges every feature branch and then runs `checks` on the result. For many branches or a slow build, raise both. A run that times out pushes nothing and tries again later.
5. **Settings.** Go through the settings that decide what the agent does, and write the user's choices to the config. Defaults and descriptions are in `scripts/defaults.toml` in this skill's directory, and models are in `models.toml`.
   - `checks`: suggest commands from the repo's CI workflows, justfile, Makefile, README or agent instructions, and confirm them with the user. For a fork with an integration branch, they should build what gets deployed. They run in the checkout, so use paths that work from there.
   - GitHub only: `deps.merge`, which update types to merge (majors only after a release-notes review). Tell the user the skill reviews no PRs but Dependabot's, and only looks after its own PRs beyond that.
   - `scan.mode`: open improvement PRs, or only record findings.
   - `report_dir`, the schedules, and `agent`, if the defaults don't suit.
6. **Agent instructions.** Every run follows the checkout's own `CLAUDE.md` or `AGENTS.md`. Claude Code also reads those of parent directories. If the user's conventions for this repo aren't written down there, suggest adding them.
7. **Dry runs.** Run `maintainer.py run . <task> --dry-run` for each enabled task. Each shows whether the task would run, and the context the agent would get. It starts no agent, pushes nothing, switches no branch and records nothing. Summarize the results: for a fork, which branches would be refreshed and whether the mirror is behind.
8. **Install.** When the user agrees, run `maintainer.py install .`, then show the timer list it prints. Tell them three things:
   - Where daily reports will appear.
   - That `maintainer.py status` shows recent runs.
   - That `maintainer.py resume . [task]` reopens a run's session, so they can ask it for a quick change.
9. **Optional first run.** Offer to start one task now with `maintainer.py run . <task>`, so they see a real run before the timers fire. It starts a headless agent in this checkout, so it takes a few minutes and costs a run. The user has to commit or stash their own changes first, or the run will be blocked.
