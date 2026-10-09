---
name: arch-pre-upgrade
description: Pre-upgrade report of breaking changes and action items since the last yay -Syu. Reports only; never upgrades.
disable-model-invocation: true
---

# arch-pre-upgrade

Decide whether it's safe to run `yay -Syu` now, and list exactly what to do before and after. Only findings that affect packages installed or pending on this system belong in the report. Generic Arch news that doesn't touch them is noise. The machine may be a desktop or a headless server; let the snapshot decide what's relevant.

## Rules

- This skill only researches and reports. Never run `yay -Syu`, `pacman -Syu`, `pacman -Sy`, or any other command that installs, removes, or upgrades packages, refreshes the sync database, or edits system files. To list pending updates, use `checkupdates` (repo packages, from pacman-contrib) and `yay -Qua` (AUR packages); neither changes the system.
- Never build, source or run anything from an AUR package. Read `PKGBUILD` and `.install` files as text only.
- Every finding needs a source: a link, a log line or command output. Mark anything you couldn't confirm as unconfirmed.
- Pick subagent models from `subagents.md` in the workspace root. Pass the model explicitly every time you start a subagent.

## 1. Snapshot

From the workspace root, run this skill's `scripts/snapshot.sh work/<YYYY-MM-DD>-pre-upgrade-snapshot.md`. It records:

- the last completed full upgrade, pacman commands run since then, and whether this session is over SSH
- pending repo and AUR updates, with old → new versions
- existing `.pacnew`/`.pacsave` files, and edited config files owned by pending packages
- kernels, boot and initramfs setup, firmware, GPU drivers, DKMS modules and desktop packages
- storage layout and running services
- repositories, packages from unofficial repos, foreign and ignored packages
- failed units and free disk space

If nothing is pending, say so and stop. Otherwise, give every subagent the snapshot path, and use the last full upgrade as the start of the period to investigate.

## 2. Research

Start steps 1, 2, 4 and 5 in parallel, then run step 3 yourself. The tier in parentheses refers to `subagents.md`.

1. **Official notices** (worker). Read archlinux.org/news and security.archlinux.org advisories published since the last full upgrade. Report items that touch an installed package. Any "manual intervention" item is a Blocker.
2. **Community reports** (worker). Search the Arch forums, r/archlinux, gitlab.archlinux.org issues and upstream bug trackers for breakage reports about the pending versions. Only pending packages are in scope.
3. **Package changes** (you, with one worker per group). Group the pending repo and AUR updates: kernel/firmware/boot, graphics, desktop, core system, services and storage, languages/toolchains, and everything else. Skip groups with no pending packages. Give each worker its group's packages with old → new versions, and `references/breakage-patterns.md`. Each worker reads upstream release notes and changelogs between the two versions for breaking changes, removed or renamed options, config migrations and required rebuilds. For packages with user or service config (a compositor, `sshd`, `smb.conf`), it compares removed options against the actual config files. Packages whose version only changes in `pkgrel` are Arch rebuilds; skip their upstream changelogs unless they're on the breakage-patterns list.
4. **AUR audit** (lead). Follow `references/aur-audit.md` for every pending AUR update.
5. **Config files** (worker). For each existing `.pacnew`/`.pacsave`, show what differs and recommend keep, replace or merge. For each edited config file owned by a pending package, say a new `.pacnew` may appear and what to watch for when merging. Use the critical-files list in `references/breakage-patterns.md`.

### What every subagent returns

One entry per finding:

- **Finding:** what changes or breaks, in one or two sentences
- **Packages:** affected packages, old → new
- **Severity:** Blocker, Before, After or FYI
- **Source:** link, log line or command output
- **Action:** the exact command or step, if there is one

Then a "Checked, nothing found" list of what was examined with no findings.

Severity:

- **Blocker:** upgrading now is likely to break the system. Don't upgrade until it's resolved.
- **Before:** do this before upgrading.
- **After:** do this after upgrading, such as merging a config file, rebuilding a package or rebooting.
- **FYI:** worth knowing; no action needed.

## 3. Merge

- Combine duplicate findings. News and community reports often describe the same issue.
- Drop findings that don't touch a package installed or pending here.
- Check every Blocker against a second source. If only one source supports it, downgrade it to Before and say so.
- If the session is over SSH, raise anything that could cut off remote access or stop the machine from booting to at least Before (see Remote access in `references/breakage-patterns.md`).
- Order the Before and After items so the user can follow them top to bottom.

## 4. Report

Write `reports/<YYYY-MM-DD>-arch-pre-upgrade.md` using `references/report-template.md`. In chat, give only the verdict, the Blockers and Before items, and the report path.
