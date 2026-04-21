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

## Folder Structure
src/
tests/