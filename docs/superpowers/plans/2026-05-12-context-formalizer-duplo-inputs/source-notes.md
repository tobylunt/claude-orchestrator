# Source Notes

## Blog Post

Source: https://www.thetypicalset.com/blog/thoughts-on-coding-agents

Published: April 29, 2026.

Relevant takeaways:

- Coding agents reduce the cost of implementation, which moves the bottleneck
  toward roadmap quality, acceptance criteria, and the shared decision record.
- Organizations run on implicit context: why a system exists, which decisions
  are load-bearing, what was tried and rejected, and where current docs are
  stale.
- Agents do not absorb context by osmosis. Context must be made explicit in
  prompts, files, tools, and instructions.
- Agents are unusually good at exhaustive reading, so one useful loop is a
  context-producing agent that crawls repos, issues, PRs, comments, and docs to
  produce a knowledge base other agents and humans can read.
- The output will remain partial. The right target is a useful, cited starting
  point rather than complete recovery of organizational memory.

## Presentation Screenshots

Screenshot: `Screenshot 2026-05-12 at 10.55.32 AM.png`

Actionable ideas visible in the feature table:

- Self-healing apps: runtime failures can trigger fix sessions.
- Automatic bug audit: an audit sweep finds, verifies, and fixes defects.
- Investigation mode: surviving bugs need diagnostic loops, not blind retries.
- Continuous code review: a second model reviews every commit in parallel.
- Multi-model patterns: route edits through structured workflows.
- Maintain mode: project invariants should run in full sessions.
- Interrupt and resume: interrupted work should persist state and continue.
- Failed-approach tracking: `[RULEDOUT]` markers prevent repeated dead ends.
- Bug-only mode: unchecked bugs should preempt feature work.
- Continuous execution: fresh sessions plus rolling summaries preserve momentum
  without carrying stale context.

Screenshot: `Screenshot 2026-05-12 at 10.43.08 AM.png`

Visible work principles:

- Design before code.
- Test before approve.
- Redesign when reality changes.
- Commit as if the system depended on it.
- Audit before merge.
