# Domain Docs

How engineering skills should consume this repository's domain documentation.

## Before exploring, read these

- `CONTEXT.md` at the repository root, when it exists.
- Relevant ADRs under `docs/adr/`, when they exist.

If these files do not exist, proceed silently. Domain-modeling skills create
them lazily when terminology or architectural decisions are resolved.

## File structure

This is a single-context repository:

```text
/
├── CONTEXT.md
├── docs/adr/
└── scripts/
```

## Use the glossary's vocabulary

When an issue, test, or implementation names a domain concept, use the term
defined in `CONTEXT.md`. Do not drift to synonyms that the glossary excludes.

If the required concept is missing, reconsider whether new language is needed
or record the gap for domain modeling.

## Flag ADR conflicts

Surface any proposed change that contradicts an existing ADR instead of
silently overriding the decision.
