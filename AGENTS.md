# Development instructions

Read README.md, docs/mvp-spec.md, docs/local-contracts.md, and applicable ADRs
before changes. ADRs remain proposed until explicit maintainer acceptance after
independent review; implementing a prototype does not change status.

## Roles and change ownership

- Main Sol coordinates requirements, architecture, Issues, deadlines, integration
  and publication. Verify the actual selected model. Requested implementation
  model is gpt-6.1-terra; independent reviewer is gpt-6.1-sol. Report unavailability
  explicitly; never silently substitute a model.
- Terra implements a bounded Issue in an isolated branch/worktree. Handoff names
  requirements, allowed files, acceptance criteria, checks and expected results.
- Independent Sol reviews the exact diff/base/head commit SHAs, reads the
  requirements and code independently, and runs risk-based checks as needed.
  Reviewer reports findings and verdict; it does not edit implementation, publish,
  merge, close Issues, or call WorkTime. Terra must not call WorkTime either.
- Main Sol arranges fixes through Terra and repeat review, verifies integration,
  and publishes. Never let two agents edit the same files concurrently.
- Agent review is technical evidence, not another GitHub account's approval.

## Issues, deadlines and completion

Inspect current files, branches/remotes, GitHub Issues/PRs/milestones and applicable
instructions on every resumed session. Check for duplicates before creating work.
Each Issue includes problem/outcome, scope, dependencies/public ADR links,
testable acceptance criteria/checks, priority, uncertain effort estimate, original
and current YYYY-MM-DD deadlines with Europe/Moscow and explicit end-of-day when
time is omitted. Use milestone dates; state when issue deadlines are body text.
Rescheduling retains original date, reason, new date and dependency impact.
Flag overdue work on resumption. No implied execution between active sessions.

Start only ready work. Review contracts before substantial implementation.
Keep slices small; test behavior/errors/invariants according to risk. Required
checks for a code slice include go test ./..., go test -race ./..., go vet ./...
and CLI build unless the Issue documents why a check cannot run. Review does not
replace tests or vice versa. Close an Issue only after criteria are satisfied,
integration into main, and successful required checks. Partial work stays open.

## Public content and Git

This repository is public. Publish self-contained technical requirements,
alternatives, rationale and consequences only. No private correspondence,
personal research/planning notes, attribution to private advice, links to private
context repositories, or credentials in documents, Issues, PRs, comments or commit
messages. Review staged diff and commit text before publication. Send bounded
technical handoffs to agents; do not include private material unnecessarily.

Prefer github_personal MCP; verify get_me is m1ssha before MCP writes. Inspect
branch protection and PR templates. Expected Git author is Mikhail Nechaev
<100868689+m1ssha@users.noreply.github.com>; run git var GIT_AUTHOR_IDENT before
committing and git remote -v before pushing. Keep user.useConfigOnly=true.
Use git@github-personal:m1ssha/scientific-mlops-core.git and personal SSH profile.
Never print/store tokens or change repository visibility.

Only the main agent may attribute WorkTime, using the exact GitHub Issue title
and native key #N. If tools are unavailable, record that limitation without
claiming time attribution. GitHub is the task-status source of truth; the main
agent maintains private continuation context separately.
