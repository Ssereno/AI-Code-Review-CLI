---
agent: agent
tools:
  - search/codebase
  - edit/editFiles
description: Implements a new Python feature
---

Act as a Developer Agent.

## Task
Implement the described feature following these rules:

1. Analyze the existing code in `src/` to understand conventions
2. Create the required files in `src/`
3. Reuse existing classes and utilities whenever possible
4. Use Pydantic for input data validation

## Checklist Before Finishing
- [ ] No type errors: `mypy src/`
- [ ] No style violations: `ruff check src/`
- [ ] No `Any` usage without justification
- [ ] Environment variables documented in `config.yaml`
- [ ] Docstrings on all public methods (Google Style)

## Feature to Implement
[DESCRIBE HERE WHAT YOU WANT TO IMPLEMENT]