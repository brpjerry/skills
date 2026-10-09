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

## Layout

```
.
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
- A workspace skill can't reuse a shared skill's name.
- Session output belongs in `<workspace>/work/`, which is gitignored.
