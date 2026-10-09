set shell := ["bash", "-euo", "pipefail", "-c"]

# List recipes
default:
    @just --list

# Enable the repo's git hooks (once per clone)
setup:
    git config core.hooksPath .githooks

# Validate workspace layout and SKILL.md frontmatter
check:
    python3 scripts/check.py

# Create a workspace: a directory you start claude/codex sessions in
new-workspace name:
    #!/usr/bin/env bash
    set -euo pipefail
    name='{{name}}'
    if [[ ! $name =~ ^[a-z0-9]+(-[a-z0-9]+)*$ ]]; then
        echo "workspace name must be lowercase-hyphenated" >&2; exit 1
    fi
    if [[ $name == skills || $name == scripts ]]; then
        echo "'$name' is reserved" >&2; exit 1
    fi
    if [[ -e $name ]]; then
        echo "$name already exists" >&2; exit 1
    fi
    mkdir -p "$name/skills" "$name/.claude" "$name/.agents"
    touch "$name/skills/.gitkeep"
    ln -s ../skills "$name/.claude/skills"
    ln -s ../skills "$name/.agents/skills"
    cat > "$name/AGENTS.md" <<EOF
    # $name

    TODO: what this workspace is for and what every session here should know.

    Put generated files and other session output in \`work/\` (gitignored).
    EOF
    printf '@AGENTS.md\n' > "$name/CLAUDE.md"
    echo "created $name/ — fill in $name/AGENTS.md, then: just new-skill $name <skill>"

# Create a skill in a workspace; use "." as the workspace for a shared skill
new-skill workspace name:
    #!/usr/bin/env bash
    set -euo pipefail
    ws='{{workspace}}'; name='{{name}}'
    if [[ ! -d $ws/skills ]]; then
        echo "no workspace at $ws (create it with: just new-workspace $ws)" >&2; exit 1
    fi
    if [[ ! $name =~ ^[a-z0-9]+(-[a-z0-9]+)*$ || ${#name} -gt 64 ]]; then
        echo "skill name must be lowercase-hyphenated and at most 64 characters" >&2; exit 1
    fi
    dir="$ws/skills/$name"
    if [[ -e $dir ]]; then
        echo "$dir already exists" >&2; exit 1
    fi
    mkdir -p "$dir"
    cat > "$dir/SKILL.md" <<EOF
    ---
    name: $name
    description: TODO what this skill does and when to use it.
    ---

    # $name

    TODO
    EOF
    echo "created $dir/SKILL.md"
