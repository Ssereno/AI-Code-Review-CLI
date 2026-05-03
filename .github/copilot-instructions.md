# Copilot Global Instructions

## Project
- Stack: Python + pytest
- Code style: PEP8, Black formatter
- Linting: Ruff or Flake8

## General Rules (all agents)
- Never use `Any` without justification — use explicit types with `typing`
- Never hardcode secrets — use `python-dotenv` + environment variables
- All public methods must have docstrings (Google Style format)
- Type hints are mandatory for all parameters and return values
- Use English names for code and comments

## Error Handling
- Never use bare `except:` — always catch specific exceptions
- Log exceptions with context; do not silently swallow errors
- Use custom exception classes for domain-specific errors

## Security
- Never hardcode secrets (tokens, passwords, API keys) in source code
- Use `python-dotenv` to load secrets from `.env` files (must be in `.gitignore`)
- Validate and sanitize all external inputs at system boundaries
- Avoid `eval()` or `exec()` — prefer safe alternatives

## Functions and Methods
- Functions should do one thing (Single Responsibility Principle)
- Keep functions under 30 lines; refactor if longer
- Never use mutable default arguments — use `None` as default and initialise inside the function

## Folder Structure
src/
tests/