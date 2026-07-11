# Skill Index

Primary skill directory for RoboData. Load one primary skill per task; compose with at most one review/safety lens when justified.

## Skill Routing

| Trigger | Skill | Compose With |
|---------|-------|--------------|
| Non-trivial code write, feature, refactor, restructuring | `code-craft` | — |
| Bug, failure, "why is this happening", root cause | `systematic-investigation` | `code-craft` (if fix requires new code) |
| Unfamiliar codebase navigation, "where is X", trace flow | `codebase-exploration` | — |
| Explicit review, auth/secrets/data handling, parsers, validators, branching logic | `reviewer` | — |
| Specs, PRD, TRD, acceptance criteria, user stories, scope definition | `requirements-driven-dev` | `architecture-writer` (if new system) |
| Architecture doc, system design, data flow diagrams, API contracts | `architecture-writer` | — |
| Bootstrap project, AGENTS.md, GLOSSARY.md, Makefile, CI/CD skeleton | `project-foundation` | — |

## Composition Rules

- Always-on for implementation: `code-craft` enforces SOLID, KISS, modularity, readability.
- Auth/security changes: compose with `reviewer` (security lens).
- Queue/store/data-access changes: compose with `reviewer` (edge-case lens).
- New subsystem or cross-cutting concern: use `codebase-exploration` first to map existing surface area.

## Skill Location

All skills live under `~/.agents/skills/` (global) or `.agents/skills/` (project-local). Local overrides global.
