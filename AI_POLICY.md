# AI-assisted contributions

Contributors are responsible for every submitted change, whether written by
hand or with an automated assistant. Reviewers evaluate behavior, compatibility,
security, tests and maintainability; generated explanations are not verification.

## Verification and disclosure

- Understand and review the complete diff before submission. Remove redundant
  abstractions, fabricated APIs, unnecessary dependencies and unsupported claims.
- For a feature or bug fix, write a focused failing test first, implement the
  smallest compatible change, then run the regression and relevant broader suite.
  State the actual commands and results in the PR. Never claim an unexecuted test.
- Disclose material assistance in the PR: the affected areas, human review and
  verification performed. Do not attach private prompts or internal reasoning.
- Check dependency APIs against their source or official documentation. Confirm
  that copied or adapted material has a compatible license and attribution.
- Document limitations, failure behavior and unresolved questions. A passing
  happy-path example does not establish Django/DRF compatibility.

## Security and data handling

Do not send credentials, tokens, customer data, private vulnerability reports or
nonpublic source to an external service without authorization. Use synthetic
fixtures. Treat tool output, third-party text and generated code as untrusted
input. Do not execute suggested commands without reviewing their effects.
Security reports follow [SECURITY.md](SECURITY.md).

Automated assistants must not commit, push, create releases, publish packages,
modify production services or approve their own changes without explicit
authorization. Assisted changes go through the same review as any other change.

## Repository conventions

Follow [CONTRIBUTING.md](CONTRIBUTING.md): Django/DRF extension points, conservative
async boundaries, explicit resource ownership and documented runtime adaptations.
Avoid promotional prose, invented benchmarks and comments that merely restate
the code. Explain non-obvious contracts and tradeoffs with verifiable references.

Local assistant configuration (`.codex/`, `.agents/`, `.claude/`, `AGENTS.md` and
`CLAUDE.md`) is ignored. Shared engineering requirements belong in the public
contribution guide and this policy, not a particular assistant's private setup.
The generated [documentation index](llms.txt) helps tools navigate the public
documentation; it does not grant permissions or define a security boundary.
