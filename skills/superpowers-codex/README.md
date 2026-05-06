# superpowers-codex

Superpowers skills for Claude Code with **OpenAI Codex** as the coding backend via [mcp-codex-worker](https://www.npmjs.com/package/mcp-codex-worker).

## Architecture

Claude Code acts as CTO -- it handles brainstorming, planning, research, and code review. Codex agents handle the actual coding and testing via MCP.

| Role | Who | How |
|------|-----|-----|
| Brainstorming | Claude | Native reasoning |
| Planning | Claude | Native reasoning |
| Research | Claude | Explore, Plan, internet-researcher agents |
| **Coding** | **Codex** | `codex-thread-start` + `codex-turn-start` (gpt-5.4, reasoning: high) |
| **Testing** | **Codex** | Same thread-based execution |
| Spec review | Claude | Native `Agent(superpowers:code-reviewer)` |
| Code quality review | Claude | Native `Agent(superpowers:code-reviewer)` |

## Skills Included

| Skill | Purpose |
|-------|---------|
| using-superpowers | Skill routing and discovery |
| brainstorming | Collaborative design before implementation |
| writing-plans | Detailed implementation plans |
| subagent-driven-development | Execute plans via Codex threads |
| executing-plans | Alternative inline execution via Codex |
| test-driven-development | TDD discipline (injected into agent prompts) |
| systematic-debugging | Root cause investigation |
| verification-before-completion | Evidence before claims |
| requesting-code-review | Dispatch Claude code reviewer |
| receiving-code-review | Handle review feedback |
| finishing-a-development-branch | Merge, PR, or cleanup |

## Requirements

- Claude Code with MCP support
- [mcp-codex-worker](https://www.npmjs.com/package/mcp-codex-worker) MCP server configured
- Codex authentication (`codex login`)

## Default Model

`gpt-5.4` with `reasoningEffort: high`
