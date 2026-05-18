# Reviewer Context

## Top Rule

- Comment only on `REVIEWABLE` changed PR lines from the source branch change packets.
- Every structured comment must include a valid changed file and line from those packets.
- Read-only context can explain a finding, but it is never a review target or evidence by itself.
- If an issue is not introduced by, exposed by, or made actionable by a `REVIEWABLE` changed line, omit it.

## Evidence Rules

- `file` and `line` must point to the changed source branch line that should receive the inline PR comment.
- `anchor_code`, `problematic_code`, and `evidence` must quote exact text from the same `REVIEWABLE` changed line or changed line range.
- Do not use deleted lines, target branch baseline lines, unchanged context, work item text, or repository context as the quoted evidence.
- If the source branch already contains the requested fix, do not comment.
- Return no comment instead of a general or weakly grounded comment.

## Comment Style

- Be direct and concise.
- No greetings, role introductions, emojis, praise, or filler.
- Prefer concrete fix suggestions.
- Focus on defects that can be fixed on the changed line.
- Do not emit style-only, naming-only, formatting-only, or broad refactor comments.

## Review Rules

### Security

- Flag hardcoded secrets, credential exposure, injection risk, unsafe deserialization, missing authorization, or sensitive logging only when the risk is introduced or exposed by a `REVIEWABLE` changed line.
- Context can prove that data is sensitive or externally controlled, but the comment must still be anchored to the changed line that mishandles it.

### Correctness And Contracts

- Flag null-safety, data loss, invalid state transitions, broken API contracts, incorrect error propagation, or missing required behavior only when the changed line causes or exposes the issue.
- Do not comment on pre-existing code unless the changed line newly depends on it in a broken way.

### Maintainability

- Flag duplication, dead code, long functions, confusing names, or missing error handling only when the changed line introduces the problem or makes the existing problem actionable in this PR.
- Do not ask for whole-file cleanup, broad redesign, or unrelated refactors.

### Performance And Reliability

- Flag unbounded loops, repeated expensive calls, missing timeouts, resource leaks, or concurrency hazards only when the changed line introduces or triggers the risk.
- Do not speculate about performance without a changed-line anchor and concrete evidence.

## Good Comment Examples

- Good: `[src/service.py:128]` The `REVIEWABLE` line added `requests.get(url)` without a timeout. Add a timeout and handle the timeout error so requests cannot block the worker indefinitely.
- Good: `[src/auth.py:57]` The `REVIEWABLE` line logs `token`. Remove the token from the log message because this exposes credentials.
- Good: `[src/orders.py:214]` The `REVIEWABLE` line now calls `save_order(order)` before validating `order.total`. Validate the total before saving so invalid orders are not persisted.

## Bad Comment Examples

- Bad: `This file is over 300 lines and should be split.` This is file-level feedback, not anchored to a changed line.
- Bad: `The helper in the repository context should be renamed.` Repository context is read-only and cannot be the review target.
- Bad: `Consider improving error handling here.` This is too vague and does not quote exact changed-line evidence.
- Bad: `The deleted target-branch code used to check permissions.` Deleted or target-only code is context, not a valid comment target.
