# Subagent models

Skills in this workspace assign each subagent a tier. This file maps tiers to models, so it's the only place to update when models change. Pass the model explicitly every time you start a subagent; otherwise it inherits the main session's model.

| Tier | Use for | Claude Code | Codex |
|---|---|---|---|
| lead | Judgment-heavy work: security review, deciding what's a blocker | `claude-opus-5-5` | `gpt-6.1-sol` |
| worker | Reading and summarizing: news, changelogs, forum threads, config diffs | `claude-haiku-5-5` | `gpt-6-luna` |

Run the main session on the lead model too, since it coordinates the subagents and merges their findings: `claude --model claude-opus-5-5` or `codex -m gpt-6.1-sol`.
