# Implementer Agent Prompt Template (Codex)

Use this template when dispatching a Codex coder agent via `codex-thread-start` + `codex-turn-start`.

**Step 1:** Create thread with developer instructions:
```
mcp__codex-worker__codex-thread-start:
  model: "gpt-5.4"
  developer_instructions: |
    Follow TDD when the task specifies it.
    Commit after each completed feature.
    Report your status when finished: DONE | DONE_WITH_CONCERNS | BLOCKED | NEEDS_CONTEXT.
    If stuck, say so immediately. Bad work is worse than no work.
```

**Step 2:** Start turn with the full task prompt:
```
mcp__codex-worker__codex-turn-start:
  thread_id: "<from step 1>"
  user_input: |
    You are implementing Task N: [task name]

    ## Task Description

    [FULL TEXT of task from plan - paste it here, don't make agent read file]

    ## Context

    [Scene-setting: where this fits, dependencies, architectural context]

    ## Before You Begin

    If you have questions about the requirements, approach, or dependencies -- ask them now.

    ## Your Job

    Once clear on requirements:
    1. Implement exactly what the task specifies
    2. Write tests (following TDD if task says to)
    3. Verify implementation works
    4. Commit your work
    5. Self-review (see below)
    6. Report back

    ## Code Organization

    - Follow the file structure defined in the plan
    - Each file should have one clear responsibility
    - Follow existing patterns in the codebase
    - If a file grows beyond the plan's intent, stop and report as DONE_WITH_CONCERNS

    ## When You're Stuck

    It is always OK to stop. Report back with status BLOCKED or NEEDS_CONTEXT.

    ## Before Reporting Back: Self-Review

    **Completeness:** Did I implement everything? Miss any requirements? Edge cases?
    **Quality:** Clean, maintainable, good names?
    **Discipline:** YAGNI? Only built what was requested?
    **Testing:** Tests verify real behavior? Comprehensive?

    ## Report Format

    - **Status:** DONE | DONE_WITH_CONCERNS | BLOCKED | NEEDS_CONTEXT
    - What you implemented
    - Test results
    - Files changed
    - Self-review findings
    - Any issues or concerns
```

**Step 3:** Wait and approve:
```
mcp__codex-worker__codex-wait(operation_id: "<from step 2>")
# If pending requests: codex-request-list → codex-request-respond → codex-wait again
```

**Step 4:** Read results:
```
mcp__codex-worker__codex-thread-read(thread_id: "<id>", include_turns: true)
```
