---
name: arch-upgrade-check
description: Pre-upgrade report of breaking changes and action items since the last yay -Syu. Reports only; never upgrades.
disable-model-invocation: true
---

# arch-upgrade-check

This skill only researches and reports. Never run `yay -Syu`, `pacman -Syu`, `pacman -Sy`, or any other command that installs, removes, or upgrades packages, refreshes the sync database, or edits system files. To list pending updates, use `checkupdates` (repo packages, from pacman-contrib) and `yay -Qua` (AUR packages); neither changes the system.

## Last Upgrade

Find when the last full system upgrade ran: the timestamp on the last `starting full system upgrade` line in `/var/log/pacman.log`. Give this date to every subagent as the start of the period to investigate.

## Research Process

Spin up 5 subagents:

1. Investigate the wider internet for breaking Arch Linux changes, between the last time a full system upgrade (`yay -Syu`) was run, and now.

2. Read the entries on the Arch release blog, and surface any alerts or action items.

3. Individual package investigation: higher level (opus 5.5/6.1 sol) orchestrator fans out additional cheaper subagents (haiku 5.5/6 luna) to investigate changes to individual package groups to find breakages.

4. Check AUR packages with pending updates (`yay -Qua`) for any signs of malicious activity.

5. Audit the system for any pacnew files, and notify of incoming pacnew files with recommended action items.

## Report Format

After all subagents return, collect findings into a markdown report that gives concise action items, and possible breakages.
