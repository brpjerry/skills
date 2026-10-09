#!/usr/bin/env bash
# Read-only snapshot of the system state that arch-pre-upgrade's research starts from.
# Usage: snapshot.sh [output.md]   (prints to stdout without an argument)
# Nothing here needs root, refreshes the sync database, or changes the system.
set -uo pipefail

if [[ $# -gt 0 ]]; then
    mkdir -p "$(dirname "$1")"
    exec >"$1"
fi

repo_err=$(mktemp)
aur_err=$(mktemp)
trap 'rm -f "$repo_err" "$aur_err"' EXIT

# Print stdin as a fenced block, or "(none)" if it's empty.
block() {
    local text
    text=$(cat)
    if [[ -n $text ]]; then
        # shellcheck disable=SC2016  # literal Markdown fences
        printf '```\n%s\n```\n' "$text"
    else
        echo "(none)"
    fi
}

section() { printf '\n## %s\n\n' "$1"; }
subsection() { printf '\n### %s\n\n' "$1"; }
count() { if [[ -n $1 ]]; then wc -l <<<"$1"; else echo 0; fi; }

# Print a pending-updates list, or why it couldn't be read.
pending() {
    local list=$1 errfile=$2
    if [[ -n $list ]]; then
        block <<<"$list"
    elif [[ -s $errfile ]]; then
        echo "Failed to check:"
    else
        echo "(none)"
    fi
    if [[ -s $errfile ]]; then
        block <"$errfile"
    fi
}

# pacman logs "starting full system upgrade" before its confirmation prompt, so only
# count a start that's followed by a transaction before the next pacman command.
read -r last_line last_upgrade < <(awk '
    /\[PACMAN\] Running / { candidate = "" }
    /\[PACMAN\] starting full system upgrade/ { candidate = substr($1, 2, length($1) - 2); candidate_line = NR }
    /\[ALPM\] transaction started/ && candidate != "" { last = candidate; last_line = candidate_line; candidate = "" }
    END { print last_line, last }
' /var/log/pacman.log 2>/dev/null)
repo=$(checkupdates 2>"$repo_err")
aur=$(yay -Qua 2>"$aur_err")
mapfile -t pending_names < <(printf '%s\n%s\n' "$repo" "$aur" | awk 'NF {print $1}')

echo "# Pre-upgrade snapshot"
echo
echo "- Taken: $(date -Iseconds) on $(uname -n)"
echo "- Last full upgrade: ${last_upgrade:-not found in /var/log/pacman.log}"
echo "- Pending: $(count "$repo") repo, $(count "$aur") AUR"
echo "- Connected over SSH: $(if [[ -n ${SSH_CONNECTION:-} ]]; then echo yes; else echo no; fi)"

section "pacman commands since the last full upgrade"
echo "A sync without an upgrade (\`-Sy <pkg>\`, \`-S -y\`) here means a partial upgrade."
echo
if [[ -n ${last_line:-} ]]; then
    tail -n +"$((last_line + 1))" /var/log/pacman.log | grep -F '[PACMAN] Running ' | sed 's/ \[PACMAN\] Running / /' | block
else
    echo "(no completed full upgrade in /var/log/pacman.log)"
fi

section "Pending repo updates"
pending "$repo" "$repo_err"

section "Pending AUR updates"
pending "$aur" "$aur_err"

section "Config files"
subsection "Existing .pacnew and .pacsave files"
pacdiff -o 2>&1 | block
subsection "Edited config files owned by pending packages (may get a new .pacnew)"
if ((${#pending_names[@]})); then
    pacman -Qii "${pending_names[@]}" 2>/dev/null | awk '
        /^Name/ { name = $3 }
        /^Backup Files/ { inbackup = 1; sub(/^Backup Files *: */, "") }
        /^[A-Z][A-Za-z ]+:/ && !/^Backup Files/ { inbackup = 0 }
        inbackup && /\[modified\]/ { print name ": " $1 }
    ' | block
else
    echo "(none)"
fi

section "Kernels and boot"
echo "- Running kernel: $(uname -r)"
echo "- /boot filesystem: $(findmnt -no FSTYPE /boot 2>/dev/null || echo 'not a separate mount')"
echo "- systemd-boot-update.service: $(systemctl is-enabled systemd-boot-update.service 2>/dev/null || true)"
current=$(efibootmgr 2>/dev/null | awk '/^BootCurrent/ {print $2}')
echo "- Current EFI boot entry: $(efibootmgr 2>/dev/null | grep "^Boot${current:-none}" | cut -f1)"
subsection "Installed kernels"
for f in /usr/lib/modules/*/pkgbase; do
    [[ -r $f ]] && pacman -Q "$(<"$f")" 2>/dev/null
done | sort -u | block
subsection "Boot, initramfs and firmware packages"
pacman -Q | grep -E '^(systemd|mkinitcpio|dracut|booster|ukify|grub|limine|refind|linux-firmware[^ ]*|intel-ucode|amd-ucode) ' | block
subsection "mkinitcpio presets"
grep -Hv -e '^[[:space:]]*#' -e '^[[:space:]]*$' /etc/mkinitcpio.d/*.preset 2>/dev/null | block
subsection "DKMS modules"
if command -v dkms >/dev/null; then dkms status 2>&1 | block; else echo "dkms not installed"; fi

section "Graphics"
lspci -k 2>/dev/null | grep -A3 -E 'VGA|3D controller|Display controller' | grep -E 'VGA|3D|Display|in use' | block
pacman -Q | grep -E '^(lib32-)?(mesa|vulkan-[^ ]+|libva[^ ]*|intel-media-driver|intel-compute-runtime|nvidia[^ ]*|xf86-video-[^ ]+) ' | block

section "Desktop"
echo "- Session: ${XDG_CURRENT_DESKTOP:-no desktop session} (${XDG_SESSION_TYPE:-unknown})"
echo
pacman -Q | grep -E '^(hypr[^ ]*|aquamarine|sway|niri|plasma-workspace|kwin|gnome-shell|mutter|xorg-server|xdg-desktop-portal[^ ]*|wayland|xorg-xwayland|pipewire|wireplumber|uwsm|sddm|gdm|greetd) ' | block

section "Storage"
lsblk -o NAME,TYPE,FSTYPE,MOUNTPOINTS 2>&1 | block
if grep -q '^md' /proc/mdstat 2>/dev/null; then
    subsection "mdadm arrays"
    block </proc/mdstat
fi

section "Running system services"
systemctl list-units --type=service --state=running --no-legend --plain 2>&1 | awk '{print $1}' | block

section "Package sources"
subsection "Repositories"
pacman-conf --repo-list | block
subsection "Installed from repositories other than core, extra and multilib"
for r in $(pacman-conf --repo-list); do
    case $r in core | extra | multilib | *testing) continue ;; esac
    pacman -Sl "$r" 2>/dev/null | awk '/\[installed/ {print $1 "/" $2 " " $3}'
done | block
subsection "Foreign packages (AUR and locally built)"
pacman -Qm | block

section "Ignored packages"
{ pacman-conf IgnorePkg; pacman-conf IgnoreGroup; } 2>/dev/null | block

section "Failed units (baseline for arch-post-upgrade)"
subsection "System"
systemctl --failed --plain --no-legend 2>&1 | block
subsection "User"
systemctl --user --failed --plain --no-legend 2>&1 | block

section "Disk space"
df -h --output=target,size,avail,pcent / /boot 2>/dev/null | block
