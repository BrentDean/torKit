# OnionShare Clone Project Plan

## Vision

Build a Debian-first, open-source OnionShare-compatible application
whose primary purpose is education, architectural understanding, and
experimentation. The project should reproduce the behavior of OnionShare
where practical while maintaining a codebase that is intentionally
simple, modular, and easy to understand.

The project is **not** intended to compete with the official OnionShare
project or claim stronger security. Instead, it is intended to teach the
complete software stack behind anonymous file sharing over Tor and
provide a maintainable foundation for future experimentation.

------------------------------------------------------------------------

# Guiding Principles

## Primary Goals

1.  Understand every major subsystem.
2.  Keep the architecture simple.
3.  Build one feature at a time.
4.  Never sacrifice readability for cleverness.
5.  Security comes before convenience.

## Non-Goals

-   Rewriting cryptography.
-   Replacing the Tor network.
-   Creating a more anonymous protocol than Tor.
-   Adding features before understanding the existing behavior.

------------------------------------------------------------------------

# Architecture Philosophy

Every subsystem should have exactly one responsibility.

Example:

GUI → Session → Local HTTP Server → Tor Controller → Onion Service →
Transfer Engine

No module should become a "god object."

------------------------------------------------------------------------

# Coding Standards

-   Prefer files under 200 lines.
-   Files over 300 lines require a documented justification.
-   Functions should normally stay under 40--60 lines.
-   One class should solve one problem.
-   Avoid global state.
-   Avoid circular imports.
-   Every security-sensitive module must have tests.

------------------------------------------------------------------------

# Milestone 0 -- Foundation

Deliverables

-   Repository structure
-   README
-   PROJECT_PLAN.md
-   ARCHITECTURE.md
-   THREAT_MODEL.md
-   UPSTREAM_BASELINE.md
-   Initial test framework

Completion Criteria

-   Repository builds.
-   Tests execute.
-   Documentation explains project goals.

------------------------------------------------------------------------

# Milestone 1 -- Local File Server

Objective

Understand how a secure HTTP file server works without Tor.

Features

-   Share one file.
-   Bind only to localhost.
-   Random download URL.
-   Landing page.
-   Download endpoint.
-   Streaming transfer.
-   Clean shutdown.

Tests

-   File exists.
-   Missing file.
-   Invalid path.
-   Shutdown behavior.

Knowledge Learned

-   HTTP
-   sockets
-   streaming
-   request lifecycle

------------------------------------------------------------------------

# Milestone 2 -- Secure File Access

Features

-   Internal file manifest
-   Explicit allowlist
-   Canonical path validation
-   Path traversal prevention
-   Symlink policy
-   Random access token

Tests

-   ../../ traversal
-   Encoded traversal
-   Symlink escape
-   Missing token

Knowledge Learned

-   Filesystem security

------------------------------------------------------------------------

# Milestone 3 -- Transfer Engine

Features

-   Progress tracking
-   Transfer IDs
-   Cancellation
-   Automatic shutdown
-   Logging

Tests

-   Interrupted transfer
-   Client disconnect
-   Multiple downloads
-   Large files

Knowledge Learned

-   Streaming
-   generators
-   state machines

------------------------------------------------------------------------

# Milestone 4 -- Tor Integration

Features

-   Connect to system Tor
-   Authenticate
-   Bootstrap progress
-   Error handling

Tests

-   Tor unavailable
-   Authentication failure
-   Recovery

Knowledge Learned

-   Tor control protocol
-   Stem

------------------------------------------------------------------------

# Milestone 5 -- Onion Services

Features

-   Ephemeral v3 onion service
-   Port mapping
-   Automatic removal
-   Graceful shutdown

Tests

-   Create service
-   Remove service
-   Cleanup after crash

Knowledge Learned

-   Onion services
-   lifecycle management

------------------------------------------------------------------------

# Milestone 6 -- Client Authorization

Features

-   Client authorization keys
-   Private shares by default
-   Safe key display
-   No secret logging

Tests

-   Invalid authorization
-   Missing authorization

Knowledge Learned

-   Access control
-   Key management

------------------------------------------------------------------------

# Milestone 7 -- Desktop GUI

Features

-   File picker
-   Start/Stop
-   Bootstrap progress
-   Onion address
-   QR code
-   Transfer progress

Rule

The GUI contains almost no business logic.

------------------------------------------------------------------------

# Milestone 8 -- Hardening

Activities

-   Static analysis
-   Dependency review
-   Fuzz testing
-   Security headers
-   Filesystem review
-   Memory review
-   Cleanup review

Goal

Reach a level suitable for broader testing.

------------------------------------------------------------------------

# Milestone 9 -- Additional Modes

Add independently:

-   Receive
-   Website
-   Chat

Each mode must reuse the same core.

------------------------------------------------------------------------

# Milestone 10 -- Packaging

Deliverables

-   Python package
-   Debian package
-   Flatpak
-   Optional AppImage
-   Optional Windows support

------------------------------------------------------------------------

# Repository Rules

-   No feature without tests.
-   No copy-paste architecture.
-   Understand upstream before changing behavior.
-   Document every security decision.
-   Every milestone must produce a runnable program.

------------------------------------------------------------------------

# Definition of Done

A milestone is complete only when:

-   It runs.
-   Tests pass.
-   Documentation is updated.
-   The code is understandable months later.
-   The architecture remains simple.

------------------------------------------------------------------------

# Long-Term Vision

Eventually the project should become a reusable privacy platform
supporting:

-   Anonymous file sharing
-   Anonymous uploads
-   Static websites
-   Anonymous chat
-   Headless server mode
-   Plugin architecture
-   Docker integration
-   Cross-platform desktop application

The educational value of the project is as important as the finished
software. Every subsystem should be understandable by inspection without
requiring thousands of lines of code.
