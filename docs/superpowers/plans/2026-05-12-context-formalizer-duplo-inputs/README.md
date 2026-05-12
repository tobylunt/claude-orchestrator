# Context Formalizer Duplo Input Packet

This directory is intentionally rough input for Duplo. It is not the approved
implementation spec.

The goal is to let Bob draft a parser-readable spec for a local-first context
crawler/formalizer, then route that draft through human review before any
McLoop implementation begins.

## Draft Command

After this PR is merged, run:

```bash
bob draft \
  --inputs docs/superpowers/plans/2026-05-12-context-formalizer-duplo-inputs \
  --output docs/superpowers/plans/2026-05-12-context-formalizer-draft-spec.md
```

Review and edit the generated draft before running:

```bash
bob validate --inputs docs/superpowers/plans/2026-05-12-context-formalizer-draft-spec.md
```

Do not run `bob run` directly against this input directory. The artifact
contract is part of the product and needs explicit review before implementation.
