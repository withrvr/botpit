# Security Policy

botpit has real user accounts, authentication, and role-based permissions
(see `DECISIONS.md` §2 and §11), so please report security issues
responsibly rather than filing a public issue.

## Reporting a vulnerability

This is a personal project with no dedicated security email. Please report
suspected vulnerabilities privately using GitHub's built-in mechanism:

1. Go to the [repository's Security tab](https://github.com/withrvr/botpit/security).
2. Click **"Report a vulnerability"** to open a private security advisory.

This keeps the report visible only to the repository owner until a fix is
available, instead of disclosing it in a public issue or discussion.

## Scope

Anything in this repository is in scope, including but not limited to:

- Authentication, session, and role/permission handling
- The privacy gate that redacts game state per seat (`player_view`) —
  a human or agent seeing another seat's hand or another player's private
  data is a security bug, not just a functional one
- Room admission and visibility rules
- Injection, request forgery, or other standard web application issues in
  the API

## Response

There's no formal SLA at this stage, but reports will be acknowledged and
investigated as soon as possible.
