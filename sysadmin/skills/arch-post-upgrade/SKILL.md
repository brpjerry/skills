---
name: arch-post-upgrade
description: Post-upgrade check after yay -Syu covering config merges, failed services, new errors, rebuilds and reboots. Reports only; changes nothing.
disable-model-invocation: true
---

# arch-post-upgrade

Confirm the last `yay -Syu` left the system healthy, and list exactly what's left to do: merge config files, rebuild packages, reboot, and fix anything that broke. Run it right after upgrading, and again after the first reboot.

## Rules

- This skill only checks and reports. Don't run anything that needs root or changes the system: no package installs or rebuilds, no edits under `/etc`, no service restarts. Give the user the exact commands instead.
- Every finding needs a source: a log line or command output.

## 1. What changed

- Find the last completed full upgrade in `/var/log/pacman.log`: a `starting full system upgrade` line followed by `transaction started` before the next `[PACMAN] Running` line. A start with no transaction means the upgrade was declined.
- List everything that was upgraded, installed or removed from that point on.
- Collect warnings and errors logged during it: lines with `warning:` or `error:`, failed hooks and mkinitcpio errors.
- Check whether the system has rebooted since then (`journalctl --list-boots`).
- If there's a `reports/*-arch-pre-upgrade.md` from before this upgrade, read its After items and use them as a checklist. Also find the matching snapshot in `work/`.

## 2. Checks

1. **Pre-upgrade After items:** mark each one done, still to do or no longer needed.
2. **Config files:** run `pacdiff -o`. For each `.pacnew`/`.pacsave`, summarize the diff and recommend keep, replace or merge. Use the critical-files list in `../arch-pre-upgrade/references/breakage-patterns.md`.
3. **Reboot:** a reboot is needed if `/usr/lib/modules/$(uname -r)` no longer exists. Also recommend one after systemd, glibc, dbus, PAM or graphics driver updates.
4. **Rebuilds:** find foreign packages broken by library or Python version bumps. Use `checkrebuild` from rebuild-detector if it's installed. Otherwise, check foreign packages that own files under an old `/usr/lib/python3.*/site-packages`.
5. **Services:** run `systemctl --failed` and `systemctl --user --failed`. Compare them with the failed units in the pre-upgrade snapshot, so earlier failures aren't blamed on the upgrade. If the system hasn't rebooted, list running services whose packages were upgraded; they need a restart.
6. **Remote access:** if this session is over SSH and `openssh`, networking or `pam` was upgraded, tell the user to restart `sshd` if needed and confirm a new SSH login works before rebooting or closing this session.
7. **Errors:** if the system has rebooted, compare `journalctl -b -p err` with the boot before the upgrade and report new errors. If it hasn't, check `journalctl --since "<upgrade time>" -p err`.
8. **Desktop:** if a desktop was upgraded and this session runs inside it, check for config errors (Hyprland: `hyprctl configerrors`). Over SSH, list it for the user to check at the machine.
9. **Leftovers (FYI):** new orphans (`pacman -Qdt`), and packages dropped from the repos that now show up in `pacman -Qm`.

## 3. Report

Write `reports/<YYYY-MM-DD>-arch-post-upgrade.md` with:

- **Status:** Healthy | Needs attention | Broken
- **To do:** ordered steps with exact commands. Most need sudo, so the user runs them.
- **Findings:** what came up, with sources
- **Checked, nothing found**

In chat, give the status and the To do list.
