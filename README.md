# skills

Specialized skill sets for Claude Code and Codex. Each top-level directory is a
**workspace**: start a session inside it and you get that workspace's skills,
plus the shared skills at the repo root. These skills are meant to be used from
their workspace, not from other codebases.

```sh
cd <workspace>
claude    # or: codex
```

Both tools load skills from the directory they start in and from every parent
up to the repo root. A session started at the repo root sees only the shared
skills.

**Global skills** in `global/` are the exception: they're meant for other
codebases. `just link-global` symlinks each one into `~/.claude/skills` and
`~/.codex/skills`, so every Claude Code and Codex session can use them, and
`git pull` here updates them. They run only when invoked by name
(`/<name>` in Claude Code, `$<name>` in Codex).

| Global skill | What it does |
|---|---|
| `repo-maintainer` | Timer-driven maintainer for one repo (GitHub, Forgejo or plain git), run from your checkout of it. It merges verified Dependabot PRs (the only PRs it reviews), looks after its own PRs, refreshes forks (mirror, feature branches, integration branch), fixes inefficiencies, and writes a daily report. Runs are saved as sessions you can resume to ask for changes. Set it up with `/repo-maintainer setup` inside the checkout. |

## Layout

```
.
├── global/<name>/SKILL.md       # global skills, linked into ~/.claude/skills and ~/.codex/skills
├── skills/                      # shared skills, loaded in every workspace
├── .claude/skills -> ../skills
├── .agents/skills -> ../skills
├── <workspace>/
│   ├── AGENTS.md                # workspace instructions (read by Codex)
│   ├── CLAUDE.md                # @AGENTS.md (read by Claude Code)
│   ├── skills/<name>/SKILL.md
│   ├── .claude/skills -> ../skills
│   └── .agents/skills -> ../skills
├── scripts/check.py             # layout and frontmatter validation
└── justfile
```

Each skill exists once, in a plain `skills/` folder. Claude Code reads
`.claude/skills` and Codex reads `.agents/skills`, and both are symlinks to
that folder.

## Working on the repo

```sh
just setup                         # once per clone: run `just check` before each commit
just new-workspace <name>
just new-skill <workspace> <name>  # use . as the workspace for a shared skill
just new-global-skill <name>
just link-global                   # after adding a global skill
just check
```

## Conventions

- `SKILL.md` frontmatter needs `name` (lowercase-hyphenated, at most 64
  characters, same as its folder) and `description` (at most 1024 characters).
  Codex requires both.
- Claude-only frontmatter (`allowed-tools`, `disable-model-invocation`, ...) is
  fine. Codex-only metadata goes in `<skill>/agents/openai.yaml`.
- Keep `AGENTS.md`/`CLAUDE.md` out of the repo root and keep shared skills few,
  because both load into every workspace session.
- A workspace skill can't reuse a shared skill's name, and no skill can reuse a
  global skill's name.
- Global skills load into every session on the machine, so they must be
  explicit-only: `disable-model-invocation: true` in `SKILL.md` and
  `policy.allow_implicit_invocation: false` in `agents/openai.yaml`.
  `just check` enforces both. They keep their state outside this repo (for
  example under `~/.config` and `~/.local/state`).
- Session output belongs in `<workspace>/work/`, which is gitignored.
