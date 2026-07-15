# Security policy

## Supported versions

Security fixes target the latest 2.x release on `main`. The original `mangaeasy` command is a compatibility alias, not a separate security surface.

## Agent safety model

- Start MCP with one explicit mode. The router catalog is the safe default; `--all-tools` is privileged compatibility mode.
- Long jobs accept a typed, mode-visible MCP tool and validated JSON arguments. Raw CLI forwarding is not part of the MCP contract.
- Start MCP with a dedicated, repeatable `--allow-root` workspace (or accept its startup-directory default). The policy covers direct paths, nested typed jobs, configured media, and manifest-linked files; it is a same-user stdio guardrail, not an OS sandbox.
- Keep project and output roots in a dedicated workspace. Destructive cleanup requires a strict allowed root and exact target-name confirmation.
- Story and Song builds never upload as part of `--stage all`. Treat publish, account changes, and deletion as explicit human-authorized actions.
- Do not expose the ACE-Step API or MCP stdio through an unauthenticated public network bridge.

## Secrets and private media

YouTube OAuth files live below the application data directory, are atomically replaced, and receive owner-only permissions where supported. Never attach token files, client secrets, private voice references, copyrighted source pages, or unreleased audio to bug reports.

MediaConductor includes no licensed music or voice-cloning sample. Confirm rights and voice consent before generation or publication.

## Reporting

Report a vulnerability privately through GitHub's security-advisory interface for this repository. Include the affected commit, platform, minimal reproduction, and impact. Do not open a public issue containing credentials or a working destructive exploit.
