# Bob Maintain Invariants

These are operating invariants for agents and humans changing Bob. They are
not a replacement for tests or review; they are the context that should shape
specs, implementation, and audits.

## Work Principles

- Design before code.
- Test before approve.
- Redesign when reality changes.
- Commit as if the system depended on it.
- Audit before merge.

## System Invariants

- Boundary contracts must be explicit at every process, container, JSONL, env,
  cost, and resume handoff.
- Missing required values must halt loudly; silent producer/consumer mismatch
  is Bob's highest-risk bug shape.
- `Inconclusive` verifier results are not success. They either halt or enter a
  bounded YOLO retry path.
- Cost wording must match enforcement. `--max-cost` is advisory until the
  budget guard lands.
- Long-lived state must be durable and inspectable under `.bob/`.
- Generated specs and contracts are proposals until a human or explicit gate
  approves them.
- Context-bot claims require provenance. Conflicts, stale sources, and unknowns
  must be preserved rather than smoothed over.
- Agents may propose contract changes, but must not self-modify Bob's core
  contracts without human review.

## Review Checklist

- Does the change preserve the public CLI contract?
- Are new env vars, state files, and JSON fields documented and tested?
- Can a resumed run continue without re-burning Duplo or losing feature state?
- Are API and CLI costs recorded under the right run, phase, model, and feature?
- Would an unattended YOLO run fail closed if this path breaks?
