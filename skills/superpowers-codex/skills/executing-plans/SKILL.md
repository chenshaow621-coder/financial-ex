---
name: executing-plans
description: Use when you have a written implementation plan to execute with Codex agents as the coding backend
---

# Executing Plans (Codex Backend)

Load plan, review critically, dispatch coding tasks to Codex via MCP, review with Claude.

**Announce at start:** "I'm using the executing-plans skill to implement this plan with Codex agents."

**Note:** If subagents are available, use superpowers:subagent-driven-development instead -- it provides per-task review checkpoints and is the recommended execution method.

## The Process

### Step 1: Load and Review Plan
1. Read plan file
2. Review critically -- identify concerns
3. If concerns: Raise with user before starting
4. If no concerns: Create TodoWrite and proceed

### Step 2: Execute Tasks

For each coding task, dispatch via Codex MCP:

```
1. mcp__codex-worker__codex-thread-start(model: "gpt-5.4", developer_instructions: "...")
2. mcp__codex-worker__codex-turn-start(thread_id: "...", user_input: <task instructions>)
3. mcp__codex-worker__codex-wait(operation_id: "...")
4. If pending requests: codex-request-list → codex-request-respond → codex-wait
5. mcp__codex-worker__codex-thread-read(thread_id: "...", include_turns: true)
```

For each task:
1. Create thread and start turn
2. Wait and approve any requests
3. Read results and verify
4. Mark as completed in TodoWrite

### Step 3: Review

After all tasks complete, request code review using Claude native agent:
```
Agent(subagent_type: "superpowers:code-reviewer", prompt: <review prompt>)
```

### Step 4: Complete Development

After all tasks complete and review passes:
- **REQUIRED SUB-SKILL:** Use superpowers:finishing-a-development-branch
- Follow that skill to verify tests, present options, execute choice

## When to Stop and Ask for Help

**STOP executing immediately when:**
- Hit a blocker (agent fails repeatedly, instruction unclear)
- Plan has critical gaps
- Verification fails repeatedly

**Ask for clarification rather than guessing.**

## Remember
- Review plan critically first
- Follow plan steps exactly
- Don't skip verifications
- Stop when blocked, don't guess
- Never start implementation on main/master without user consent
- Always approve pending requests -- agents get stuck waiting
