# UPSTREAM_ARCHITECTURE.md

# OnionShare Upstream Architecture Reference

> Purpose: A living reverse-engineering reference for the official
> OnionShare project. We document the architecture before making any
> modifications.

## Guiding Rule

> We do not rewrite code we do not yet understand.

## Repository Overview

    onionshare-upstream
    ├── cli/          # Shared application core
    ├── desktop/      # Qt desktop interface
    ├── docs/         # Documentation
    ├── flatpak/      # Flatpak packaging
    ├── snap/         # Snap packaging
    ├── licenses/     # Third-party licenses
    └── build/release tooling

### CLI / Core

`cli/onionshare_cli/`

Responsibilities: - Tor integration - Onion service lifecycle - Local
web server - Share, Receive, Website and Chat modes - Settings - Common
utilities

### Desktop

`desktop/onionshare/`

Responsibilities: - Qt GUI - Windows, tabs and dialogs - Threads -
Status display - User interaction

The desktop consumes the shared core rather than implementing its own
copy.

## High-Level Architecture

    User
     │
     ▼
    Desktop GUI
     │
     ▼
    OnionShare Core
     │
     ├── Onion (Tor)
     ├── Web
     └── Settings
          │
          ▼
       Mode implementations
          ├── Share
          ├── Receive
          ├── Website
          └── Chat

## Core Components

### onionshare.py

Purpose: - Session coordinator.

Responsibilities: - Own selected port. - Own onion hostname. -
Start/stop onion services. - Own auto-stop timer.

Does NOT implement: - Tor - Flask - File transfer - GUI

Depends on: - common.py - onion.py

Status: Understood.

### onion.py

Purpose: - Tor controller.

Expected responsibilities: - Connect to Tor - Authenticate - Bootstrap -
Create/remove onion services - Client authorization - Bundled/system Tor
management

Status: Pending review.

### web/

Shared web layer.

Files: - web.py - send_base_mode.py - share_mode.py - receive_mode.py -
website_mode.py - chat_mode.py

Expected responsibilities: - Flask - Waitress - Shared lifecycle -
Shared security - Mode dispatch

Status: Pending review.

### common.py

Utility layer.

Known responsibility: - get_available_port()

Status: Partially understood.

## Desktop Components

Pending review: - **main**.py - main_window.py - threads.py - tab/ -
widgets.py

## External Libraries

Infrastructure supplied by third parties:

-   Tor
-   Stem
-   Flask
-   Waitress
-   PySide6
-   Flask-SocketIO
-   PyNaCl
-   Requests
-   PySocks

The OnionShare project primarily provides the application logic
connecting these libraries.

## Architecture Observations

-   GUI and core are cleanly separated.
-   One Tor implementation.
-   One shared web implementation.
-   Modes extend shared infrastructure instead of duplicating it.
-   Small coordinator objects delegate work.

## Reverse Engineering Checklist

  Component           Status
  ------------------- ----------
  Repository layout   Complete
  OnionShare          Complete
  common.py           Partial
  onion.py            Pending
  web.py              Pending
  Share mode          Pending
  Receive mode        Pending
  Website mode        Pending
  Chat mode           Pending
  Desktop entry       Pending
  Threads             Pending
  Tab lifecycle       Pending

## Rules for Our Clone

1.  Understand upstream first.
2.  Document before modifying.
3.  Preserve behavior unless there is a clear reason to change it.
4.  Keep one authoritative implementation for every shared capability.
5.  Refactor only after the existing design is understood.
