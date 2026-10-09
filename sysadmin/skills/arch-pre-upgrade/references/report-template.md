# Report template

Save the report as `reports/<YYYY-MM-DD>-arch-pre-upgrade.md`. Keep every section. If a section has nothing, write "None." so the reader knows it was checked. Write commands exactly as the user should run them.

Choose the verdict this way:
- **Hold off:** any Blocker remains.
- **Upgrade after the steps below:** no Blockers, but there are Before items.
- **Safe to upgrade:** no Blockers or Before items. After items are fine.

````markdown
# Arch pre-upgrade check: <YYYY-MM-DD>

Last full upgrade: <timestamp> · Pending: <N> repo, <M> AUR · Snapshot: `work/<file>`

**Verdict:** Safe to upgrade | Upgrade after the steps below | Hold off: <reason>

## Blockers

1. **<package> <old> → <new>:** what's wrong and what would resolve it. [source](<link>)

## Before upgrading

1. <step>. Why it's needed. [source](<link>)
   ```
   <command>
   ```

## After upgrading

1. <step>. Why it's needed. [source](<link>)
   ```
   <command>
   ```

## Possible breakages

Most likely or most disruptive first. Things to watch for that need no action yet.

- **<package> <old> → <new>:** what might break and how it would show. [source](<link>)

## AUR audit

| Package | Old → New | Verdict | Notes |
|---|---|---|---|

## Config files

| File | Status | Recommendation |
|---|---|---|
| <path> | existing .pacnew / expected .pacnew | keep, replace or merge, and what to watch for |

## Checked, nothing found

- <source or check>, covering <period or packages>
````
