<!-- lang: all -->
## General Rules (all languages)
- Security: SQL injection, XSS, hardcoded secrets/credentials, missing input validation, sensitive data exposed in logs.
- Error handling: swallowed exceptions (empty catch), missing logging at critical points, generic error messages to end users.
- Readability: methods too long or with too many responsibilities (SRP), poor naming, dead/commented-out code.
- Duplication: repeated logic that should be extracted/reused.
- Testability: hard-to-mock dependencies, business logic mixed with I/O.
- Unused code: unused local variables, method parameters, private fields/methods, unreachable code, and unused imports/usings.
- Unnecessary allocations in hot paths (loops, frequently-called requests).

<!-- lang: cs,ts -->
## Performance
- Use of `StringBuilder` vs string concatenation in loops.
- Blocking synchronous operations where async should be used (I/O, network calls).

<!-- lang: cs,ts,sql -->
## Concurrency / Thread-safety
- Race conditions in shared code (static fields, singletons).
- Correct use of locks/semaphores, avoid deadlocks.
- Thread-safe collections where needed (`ConcurrentDictionary`, etc).

<!-- lang: all -->
## Configuration & Secrets
- Hardcoded values that should be in configuration (connection strings, URLs, timeouts).
- Secrets/API keys accidentally committed.

<!-- lang: cs,ts -->
## Dependencies & Compatibility
- New dependencies added without clear justification - unnecessary or outdated packages.
- Breaking changes when updating package versions.

<!-- lang: cs -->
## Tests
- Business logic changes require added/updated tests.
- Tests should verify behavior, not implementation details.
- Tests should cover edge cases (nulls, empty lists, boundary values).

<!-- lang: cs -->
## C#
- Naming Conventions & Casing: follow Microsoft's official C# naming guidelines (https://learn.microsoft.com/en-us/dotnet/csharp/fundamentals/coding-style/identifier-names).
- XML docs: follow Microsoft's official documentation guidance (https://learn.microsoft.com/en-us/dotnet/csharp/language-reference/xmldoc/).
- Async: avoid `async void` (except handlers), use `ConfigureAwait(false)` in libraries, don't block with `.Result`/`.Wait()`.
- Nullable refs: correct `?` usage, avoid unjustified `!`.
- LINQ: avoid redundant iterations, N+1 queries with EF Core.
- Disposal: proper use of `IDisposable`/`using` for resources (streams, connections, etc).
- Immutability: prefer `readonly`, records, immutable types.

<!-- lang: cs,sql -->
## Logging & Observability
- Appropriate log levels (Debug vs Info vs Warning vs Error).
- Don't log sensitive data (PII, passwords, tokens).

<!-- lang: sql -->
## Migrations / Database (given your SQL/EF stack)
- Destructive changes to columns/tables with existing data (data loss risk).
- Migrations that lock large tables in production.

<!-- lang: sql -->
## SQL
- Avoid `SELECT *`; specify columns explicitly.
- Check for parameterized queries (avoid string concatenation → SQL injection).
- Indexes: flag queries likely to cause table scans on large tables.
- Transactions: ensure multi-step critical operations are wrapped in a transaction.
- Naming conventions for tables/columns consistent with the rest of the schema.

<!-- lang: xml -->
## XML
- Validation against schema (XSD) where applicable.
- Consistent formatting/indentation.
- Avoid duplicated configuration that could be centralized.

<!-- lang: html -->
## HTML
- A11y: `alt`, `aria-*`, labels on inputs, semantic tags (`<button>` vs `<div onclick>`).
- Semantic tags where they apply (`<button>`, `<label>`, `<ul>`/`<li>`) over generic `<div>`/`<span>`. Landmarks (`<header>`/`<nav>`/`<main>`) apply to shell/layout, not feature components.
- No inline `style=""` for static styling - use the component's `.less` file. `[style.x]`/`[ngStyle]` ok for dynamic values.

<!-- lang: ts,html -->
## TypeScript / Angular
- Follow the Angular style guide (https://angular.dev/style-guide).
- Avoid `any`, prefer explicit types.
- RxJS: unsubscribe via async pipe, `takeUntil`, or `takeUntilDestroyed`.
- Use `OnPush` where it makes sense.
- Separate presentation (component) from business logic (services); avoid heavy template logic.
- Files: kebab-case + type suffix (`select-maintenance-plan-step.component.ts/.html/.less`).
- Classes: PascalCase matching file name, no type suffix (`SelectMaintenancePlanStep`, not `...Component`).
- Selectors: kebab-case with project prefix (`app-select-maintenance-plan-step`).
- `.less` files named like their component; use `:host`, avoid global style leaks.

<!-- lang: all -->
## Review Process Notes (meta)
- Distinguish blocking comments (must-fix) from suggestions (nice-to-have) - this matters a lot for an automated reviewer, so it doesn't hold up merges over trivial issues.
- If the change is purely formatting/whitespace, do not comment on it.
- Ignore auto-generated files (generated migrations, `*.designer.cs`, `node_modules`, etc.) unless they contain manually-added code.
- Flag only issues relevant to the diff/change being reviewed, not the entire file, unless the change requires it.
- Before flagging something as "missing", verify it isn't present in the surrounding unchanged context (lines without `+`/`-`). Only flag genuinely absent elements, not elements outside the shown diff hunk.
- Priority order: Security > Functional bugs > Maintainability > Style.