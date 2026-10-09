# Arch breakage patterns

Ways an Arch upgrade commonly goes wrong. Each one says what to look for in the snapshot or pending updates, and what to recommend. Recommended commands are for the user to run; this skill never runs them.

## Manual intervention news

- **Signal:** an archlinux.org/news item since the last full upgrade that names an installed package.
- **Action:** Blocker until the user has read it. Copy its steps into Before or After exactly as the news item orders them.

## Remote access

- **Signal:** the snapshot says the session is over SSH, and pending updates touch `openssh`, networking (`systemd`, `networkmanager`, `iwd`, `wireguard-tools`, `tailscale`), `pam`, the kernel, the bootloader or the initramfs. Or `/etc/ssh/sshd_config` is among the edited config files. If any of these goes wrong, the machine may not be reachable to fix it.
- **Action:** raise each such finding to at least Before. Before: confirm there's another way in (local console, KVM, or a second SSH session kept open during the upgrade). After: if `openssh` was upgraded, `sudo systemctl restart sshd`, then open a new SSH connection before closing the current one. Reboot only after the initramfs and bootloader steps succeeded.

## Keyring out of date

- **Signal:** `archlinux-keyring` is pending and the last full upgrade was months ago. Packages signed by newer keys fail with "invalid or corrupted package (PGP signature)".
- **Action:** Before: `sudo pacman -Sy archlinux-keyring && sudo pacman -Su`.

## Partial upgrades since the last full upgrade

- **Signal:** the snapshot's pacman command list shows syncs without an upgrade (`-Sy <pkg>`, `-S -y ... -- <pkg>`). Those packages were built against newer libraries than the rest of the system has.
- **Action:** FYI. The full upgrade resolves it. Mention which packages were installed this way in case they misbehave until then.

## Ignored packages

- **Signal:** `IgnorePkg`/`IgnoreGroup` entries in the snapshot, and a pending package that depends on a newer version of an ignored one.
- **Action:** Before: decide whether to upgrade the ignored package too. Arch doesn't support partial upgrades.

## Library version bumps (soname changes)

- **Signal:** a pending library whose major version changes (e.g. `icu`, `boost`, `openssl`, `libalpm` via `pacman`, `llvm-libs`), and foreign packages that link against it (`pactree -r -d1 <lib>` cross-checked with `pacman -Qm`).
- **Action:** After: rebuild the affected foreign packages with `yay -S --rebuild <pkg>`.

## Python minor version bump

- **Signal:** `python` goes from 3.X to 3.Y. Foreign packages with files in `/usr/lib/python3.X/site-packages` stop being importable (`pacman -Qoq /usr/lib/python3.X/site-packages | sort -u` cross-checked with `pacman -Qmq`).
- **Action:** After: rebuild those packages. FYI: virtualenvs and `pip --user` installs that use the system Python need recreating.

## pacman major version and yay

- **Signal:** `pacman` changes major version. yay links against `libalpm` and may stop starting.
- **Action:** After: if yay fails, rebuild it from the AUR (`git clone https://aur.archlinux.org/yay.git && cd yay && makepkg -si`).

## Kernel updates

- **Signal:** a kernel package from the snapshot is pending. Once it's upgraded, the running kernel's modules are removed, so loading a module that isn't already loaded (USB devices, filesystems, VPNs) fails until reboot.
- **Action:** After: reboot soon after upgrading.
- **Signal:** DKMS modules or out-of-tree module packages, and a kernel major version change.
- **Action:** Blocker if a needed module doesn't support the new kernel yet. Otherwise After: check the DKMS hook output.

## Bootloader updates

- **Signal:** a pending `systemd` (systemd-boot), `grub` or `limine` update. Package upgrades don't update the copy on the EFI partition.
- **Action:** After: for systemd-boot, `sudo bootctl update` unless `systemd-boot-update.service` is enabled. For GRUB, reinstall only when Arch news says to (`grub-install`, then `grub-mkconfig -o /boot/grub/grub.cfg`). For Limine, copy the new EFI binary to the EFI partition.

## Initramfs

- **Signal:** `mkinitcpio` (or `dracut`/`booster`) pending, especially a major version, or `/etc/mkinitcpio.conf` among the edited config files. Hooks get renamed or removed between versions.
- **Action:** After, before rebooting: merge `mkinitcpio.conf.pacnew` if one appears, then `sudo mkinitcpio -P`.

## Storage stack

- **Signal:** the snapshot's storage section shows mdadm arrays, LVM, LUKS, ZFS or multi-device btrfs, and the kernel or `mdadm`, `lvm2`, `cryptsetup`, `btrfs-progs` or `zfs-*` is pending.
- **Action:** ZFS: Blocker if the ZFS module packages don't support the new kernel version yet. mdadm, LVM, LUKS: After, before rebooting: confirm the matching mkinitcpio hooks (`mdadm_udev`, `lvm2`, `encrypt`/`sd-encrypt`) survive any `mkinitcpio.conf` merge.

## Databases

- **Signal:** `postgresql` changes major version, or `mariadb` is pending. A PostgreSQL major version can't read the old data directory.
- **Action:** PostgreSQL major: Blocker until there's a plan. Before: `pg_dumpall` or prepare `pg_upgrade` as the Arch wiki describes. MariaDB: After: `sudo mariadb-upgrade`.

## Long-running services and containers

- **Signal:** a pending package provides a service in the snapshot's running services (`sshd`, `smb`, `nfs-server`, `podman`, `docker`, `syncthing`, web servers). Services keep running the old version until restarted. Container runtime major versions can migrate storage or network configuration.
- **Action:** After: restart the affected services, or reboot. Check that containers came back (`podman ps -a`, `docker ps -a`). For a container runtime major version, read its upgrade notes first.

## Firmware package changes

- **Signal:** `linux-firmware*` packages split, renamed or replaced.
- **Action:** Before: follow the news item. Make sure the firmware for this hardware (see the snapshot's graphics section) stays installed.

## Low space on /boot

- **Signal:** less than about 100 MB free on `/boot` and a kernel or initramfs change pending. A full EFI partition makes mkinitcpio fail and can leave the system unbootable.
- **Action:** Before: free space, for example by removing the fallback preset.

## Config files

- **Signal:** existing `.pacnew`/`.pacsave` files, or edited config files owned by pending packages.
- **Critical files:**
  - `/etc/passwd`, `/etc/group`, `/etc/shadow`, `/etc/gshadow`: never replace. Merge only new entries.
  - `/etc/pacman.conf`, `/etc/mkinitcpio.conf`, `/etc/ssh/sshd_config`, `/etc/ssh/ssh_config`, `/etc/pam.d/*`, `/etc/environment`, `/etc/locale.gen`, `/etc/default/grub`, `/etc/makepkg.conf`: merge by hand. A wrong merge can block logins, SSH or booting.
  - `/etc/pacman.d/mirrorlist`: usually safe to replace or regenerate.
- **Action:** After: merge with `sudo pacdiff` (it uses `vim -d` by default) or by hand, starting with the critical files. Flag critical files in Config files.

## Desktop components with user config

Skip this when the snapshot shows no desktop packages.

- **Signal:** `hyprland` or another `hypr*` package is pending. Hyprland releases often rename or remove config options. Its ecosystem packages (`aquamarine`, `hyprutils`, `hyprlang`, `hyprlock`, `hypridle`, `hyprpaper`, `xdg-desktop-portal-hyprland`) must move together, so a mismatched `-git` or AUR build breaks them.
- **Action:** compare the release notes against `~/.config/hypr/`. Before: update any option that the new version removes. After: run `hyprctl configerrors`.

## Core services

- **Signal:** `systemd`, `glibc`, `dbus`/`dbus-broker`, `openssl`, `pam` or graphics drivers are pending. Running processes keep the old versions loaded.
- **Action:** After: reboot rather than continuing the session. Over SSH, follow Remote access first.

## Packages from other sources

- **Signal:** the snapshot's package sources show packages installed from repos other than core, extra and multilib, or foreign packages that aren't in the AUR. Arch news and the AUR don't cover them.
- **Action:** FYI. Note that they weren't checked, unless their upstream has release notes.

## Packages removed from the repos

- **Signal:** a package that used to come from a repo now appears in `pacman -Qm`, or yay reports it as "not in AUR".
- **Action:** FYI. It no longer gets updates; suggest a replacement if the news names one.
