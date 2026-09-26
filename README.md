# TorKit

**Containerized, operator-managed private onion services built around OnionShare.**

TorKit is a personal engineering project that adapts and extends the [OnionShare](https://github.com/micahflee/onionshare) codebase for running several private services on a Linux host. Its focus is lifecycle management, durable onion identity, operator controls, runtime permissions, observability, and explicit destructive operations—not inventing a new Tor protocol.

> **Provenance:** This is a modified OnionShare-based project, not an original implementation of OnionShare, an official OnionShare release, or an independent anonymity guarantee. OnionShare copyright notices, the GPL license in [LICENSE.txt](LICENSE.txt), upstream code, and applicable third-party licenses are retained. Changes to TorKit's container, launcher, operator console and Board should not be attributed to upstream OnionShare unless they are present there.

## What TorKit adds

- An interactive `./torkit` launcher and noninteractive reconciliation commands for containerized **chat, Board, file sharing, file receiving, and website** services.
- Per-service persistent identities that survive ordinary stop/restart cycles; explicit `reset`, `burn`, and `nuke` operations with different scopes.
- A private, loopback-bound operator console with temporary login token and an authenticated access panel.
- Protected local runtime state and credential-redacted routine logs; readiness checks distinguish service process health from verified external Tor reachability.
- A persistent, text-oriented **Board** with SQLite storage, thread/reply views, search, and bounded attachments.

The project uses Docker Compose and Python. The original OnionShare codebase remains part of the tree; this repository is not a small standalone application written from scratch.

## Run and inspect

See the [Docker service operator guide](container/README.md) for setup prerequisites, state paths, command behavior, limitations, and backup guidance.

```bash
./torkit           # interactive selection of services
./torkit status    # sensitive: private onion addresses / access credentials
./torkit health --json
./torkit ensure board
./torkit ps
```

**Do not paste `./torkit status` into public logs or issue reports.** Client authorization values, onion identities, local state, user content, and operator session tokens must remain private.

### Architecture and trust boundaries

```text
Operator (localhost / SSH tunnel)
    -> TorKit launcher or authenticated operator console
    -> Docker Compose / container supervisor
    -> OnionShare-derived service process
    -> Tor onion service (client authorization)
    -> Authorized Tor client
```

No Compose service intentionally publishes an application to a clearnet host port. The operator console listens on loopback and is intended to be accessed via an authenticated SSH tunnel. These controls do **not** make an Internet-exposed workstation or VPS automatically safe; operators remain responsible for host access, OS updates, credentials, and backups.

Board content persists on the server and is readable by the service operator and participants with the shared client authorization key. Display names are not verified identities. Uploaded images are not transcoded and may contain EXIF/GPS metadata. There is no claim of end-to-end encryption independent of Tor transport.

## Repository layout

| Path | Purpose |
| --- | --- |
| `container/` | Runtime supervisor, operator console, tests and operator guide |
| `cli/` | OnionShare-derived CLI with TorKit-specific changes |
| `desktop/`, `docs/` | Upstream-derived desktop application and documentation |
| `compose.yaml`, `Dockerfile`, `torkit` | Container runtime and launcher |
| `archive/PROJECT_PLAN.md` | Early exploratory design notes; not a description of the current upstream-derived implementation |
| `LICENSE.txt`, `licenses/` | Upstream and third-party redistribution terms |

## Publishing and operating safely

The repository contains source and non-secret defaults, **not** the generated service identity. Runtime state lives under `~/.torkit/`, including configuration and onion session files. Never commit these files, public-facing access screenshots, authentic service addresses, SSH keys, or data uploaded by users. Run a complete Git-history and artifact scan before publication. Removal from `main` does not erase an older committed secret from history.

This is a learning and operations project. For official OnionShare releases, usage, and security guidance, consult [the upstream project](https://github.com/micahflee/onionshare).
