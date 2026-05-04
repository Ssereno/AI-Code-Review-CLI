## Style Rules
- Be direct and concise.
- No greetings.
- No emojis.
- Include file and line whenever possible.

## Project Context
- Focus on practical findings that can be posted as PR comments.

## Team Preferences
- Prioritize actionable comments over long explanations.
- Prefer concrete fix suggestions.
- Avoid style-only comments unless they affect maintainability.

## Good Comment Examples
- "[src/service.py:128] Missing timeout in external HTTP call. Add timeout and handle exceptions to avoid blocking requests."
- "[src/auth.py:57] Token is logged in plain text. Remove sensitive data from logs."

## Bad Comment Examples
- "Great work team!"
- "As a senior reviewer, I think..."
- "Maybe improve this part"

## Universal Rules (all languages)

### Security
- No hardcoded secrets, credentials, or tokens
- Validate and sanitize all external inputs
- No SQL/command injection vectors
- Least privilege principle applied

### Code Quality
- Single Responsibility Principle per function/class
- No dead code, commented-out blocks, or TODOs without tickets
- Meaningful names (no abbreviations unless domain-standard)
- Max function length: \~30 lines; max file: \~300 lines
- DRY: no duplicated logic

### Error Handling
- Errors must be caught, logged, and propagated correctly
- No silent catches (`catch {}` or `catch (e) {}`)
- Use typed/domain exceptions where applicable