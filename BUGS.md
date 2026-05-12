# Bob Bug Queue

Unchecked bugs and contract gaps should preempt feature work when they threaten
silent failure, spend control, resume safety, or provenance.

## Open

- `[P1]` Hard budget enforcement is not implemented. `--max-cost` is currently
  an advisory bound and YOLO intent signal.
- `[P2]` Real-mode validation is still pending for `--vroom`, `--yolo`, and the
  devcontainer sandbox.
- `[P2]` Directory-based multimodal Duplo is wired, but needs more real-mode
  validation before it should be trusted unattended.

## Ruled Out

- `[RULEDOUT]` Do not treat paid-account CLI session parsing as a near-term
  substitute for API-backed model calls. CLI output shape is a separate adapter
  project and should wait until the API-backed system has more value.
- `[RULEDOUT]` Do not let the context formalizer promote generated org context
  to canonical docs without a human approval gate and source provenance.
