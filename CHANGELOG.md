# Changelog

## 2.0.1 — 2026-10-21

First release in this repository. The repository was empty before this version, so there is no earlier
release to upgrade from; the database starts at schema version 1.

### Added
- PySide6 desktop app with conversations, memory, reminders, workspace, tools & permissions, voice, settings and
  diagnostics screens; dark and light themes.
- Streaming chat with a local Ollama model, with cancellation, timeouts and clear error messages.
- SQLite storage with versioned migrations, pre-upgrade backups and safe handling of unreadable databases.
- User-approved long-term memory with relevance-based retrieval.
- Deny-by-default tool registry with path containment, protected-file exclusion, confirmation dialogs and an
  audit log.
- Developer workspace with file browsing, search, and diff-reviewed edits with backups.
- Local reminders shown while GREEN is running.
- Optional push-to-talk speech recognition (faster-whisper) and speech output (Piper).
- Automated test suite (pytest, pytest-qt) with an in-process fake Ollama server.

### Known limitations
See "Not implemented" and "Verification status" in README.md.
