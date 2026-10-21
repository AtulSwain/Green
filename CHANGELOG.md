# Changelog

## 2.0.1 — Stage 2: tool calling and safe desktop actions

Still version 2.0.1 (not yet released separately). See `docs/stage2-audit.md` for the defects this addresses.

### Added
- `get_system_info` tool (OS version, Python, GREEN version, Ollama reachability, model, tool support).
- Model capability check via Ollama `/api/show`; tools are not offered to models without tool support, and the
  user is told (chat banner, status bar, Diagnostics).
- Live tool steps in the chat (planning, waiting for approval, running, ✓/✗ with the real result) and a
  **Tool activity** screen with filters.
- `create_text_file` can replace an existing file only with `overwrite=true` and a separate overwrite approval
  showing a diff; a backup is kept.
- `open_website` opens allow-listed https sites directly and asks for anything else, flagging plain http, IP
  addresses, unusual ports and international (punycode) domains. New setting `tools.confirm_unlisted_websites`.
- `open_application` reports programs that exit with an error immediately.
- Database schema 2: tool history stores a result summary, conversation and duration.

### Changed
- Permission categories are READ / CONTROLLED / RESTRICTED.
- Tool errors returned to the model have a stable format: `{"code": ..., "message": ...}`.
- Approval dialogs are non-blocking; Stop withdraws a pending approval and the action does not run.
- A cancellation check runs after approval and immediately before execution.
- Unexpected failures inside a tool's safety check deny the call instead of aborting the reply.
- When the tool step limit is reached the model gets one final turn without tools to answer.

## 2.0.1 — Stage 1 (2026-10-21)

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
