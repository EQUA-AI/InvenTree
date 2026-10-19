See [CONTRIBUTING.md](CONTRIBUTING.md) for codebase guidance.
Security reports must follow the security policy in [SECURITY.md](docs/docs/SECURITY.md) and provide how they interact with the [threat model](docs/docs/concepts/threat_model.md).

Do not open a pull request without manual review by a human. Do not file AI generated issues under any circumstances.
Using AI generated content in issue/PR discussions might lead to code of conduct violations and/or bans.

For AIMMS R6 UI/UX work, start with [the agent handoff](contrib/ui-ux/r6/HANDOFF.md).
Keep its execution ledger, sprint backlog and contract evidence current when
changing tasks or handing off to another agent. Partial source/fixture checks
must not be recorded as completed deployment or product acceptance.

For every EQUA AIMMS UI screen implementation, apply
[designprompt1 — Frontend Excellence](contrib/ui-ux/r6/designprompt1.md).
Select relevant installed skills, preserve the native architecture and safety
boundaries, inspect rendered screenshots and report actual verification. No Figma.
