---
agent: agent
tools:
  - search/codebase
  - edit/editFiles
description: Complete Developer → QA → Docs pipeline for Python
---

Execute the pipeline in 3 sequential phases.
Do not move to the next phase without confirming the previous one is complete.

---

## PHASE 1 — Developer Agent
Implement the described feature.
- Save files in `src/`
- Check types: `mypy src/`
- Check style: `ruff check src/`
- Report: created files + required pip dependencies

**✅ Advancement criterion:** zero type and style errors

---

## PHASE 2 — QA Agent
Review the Phase 1 code.
- If CRITICAL or HIGH issues exist → stop and report. Phase 1 must fix them.
- If approved → generate tests in `tests/`
- Run: `pytest --cov=src --cov-report=term`
- Report: found issues + test results + coverage

**✅ Advancement criterion:** zero critical issues + all tests passing + coverage ≥ 80%

---

## PHASE 3 — Docs Agent
With code approved and tested:
- Add Google Style docstrings to source code
- Generate `README.md`
- Report: created documentation files

**✅ Completion criterion:** all artifacts generated

---

## Feature to Implement
[DESCRIBE HERE]