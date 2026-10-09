# AUR audit

Review each pending AUR update's packaging for malicious or suspicious changes. Upstream changelogs are covered by the package-changes step, not here.

Never build, install or execute anything. Read files as text. `source PKGBUILD`, `makepkg` and `makepkg --printsrcinfo` all execute the PKGBUILD; use `.SRCINFO` for metadata instead.

## Get the history

For each package in the snapshot's pending AUR list:

1. Look up its metadata: `curl -s https://aur.archlinux.org/rpc/v5/info/<pkgname>`. Note `PackageBase`, `Maintainer`, `CoMaintainers`, `Submitter`, `FirstSubmitted`, `LastModified`, `NumVotes`, `Popularity` and `OutOfDate`.
2. Clone the package base: `git clone https://aur.archlinux.org/<pkgbase>.git work/aur/<pkgbase>`.
3. Find the commit for the installed version (from `pacman -Q <pkgname>`):
   - If `~/.cache/yay/<pkgbase>` exists, its `HEAD` is usually what was built. Read it with `git -C ~/.cache/yay/<pkgbase> rev-parse HEAD`, and don't fetch or change anything in that cache.
   - Otherwise, find the commit whose `.SRCINFO` has the installed `epoch`, `pkgver` and `pkgrel`.
   - If neither works, review the last several commits and say the baseline is unknown.
4. Review everything between that commit and `HEAD`:
   - `git log --format='%h %an <%ae> %ad %s' <installed>..HEAD`
   - `git diff <installed> HEAD`

## Red flags

**Who changed it**
- A new committer, or a maintainer change. Adopting an orphaned package and then changing its sources is the classic attack.
- A committer with no other AUR history, or a recently created account.

**Where sources come from**
- New domains in `source=`: URL shorteners, raw IPs, paste sites, file hosts, chat CDNs, personal forks in place of the upstream project.
- `http://` replacing `https://`.
- `-bin` packages downloading from somewhere other than the upstream's official releases.

**Integrity**
- Checksums changed to `SKIP` for anything other than a VCS source.
- Checksums that changed without a version change.
- Binary blobs or archives committed to the AUR repo.

**What the scripts do**
- `prepare()`, `build()`, `package()` or the `.install` hooks (`post_install`, `post_upgrade`) that:
  - download anything (`curl`, `wget`, `git clone`) outside `source=`, or pipe it into a shell
  - decode or `eval` data (base64, hex, `printf` escapes) or use obfuscated names
  - write outside `$pkgdir`: `$HOME`, `/etc`, `/usr`, `/tmp` scripts that run later
  - add systemd units, timers, cron jobs, shell rc changes, SSH keys, sudoers entries or setuid binaries that the software doesn't need
  - contact the network at install time
- A large diff for what claims to be a small version bump, or a `pkgrel` bump with no visible reason.

**Community signals**
- Recent comments on `https://aur.archlinux.org/packages/<pkgname>` reporting malware, hijacking or unexpected behavior.
- Pending deletion or orphan requests.

## Verdicts

Give each package one verdict:

- **OK:** only version, checksum and routine packaging changes.
- **Review:** something unusual that's plausibly benign. Quote the diff lines and say what the user should check.
- **Don't upgrade:** evidence of malicious behavior or a compromised maintainer. Recommend `yay -Syu --ignore <pkgname>` as a Before step, and reporting the package on the AUR. If the installed version is affected too, make it a Blocker and recommend removing the package.

Return the verdicts as a table (package, old → new, verdict, notes), plus a finding for every Review and Don't upgrade, in the format from SKILL.md.
