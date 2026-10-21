# Stage 2 audit (before changes)

Audited at commit `6c0d6d2` (GREEN 2.0.1, Stage 1). Test suite at that commit: 138 passed, 2 skipped
(Windows-only checks).

## What already exists

| Area | Where | State |
| --- | --- | --- |
| Ollama integration | `green/services/ollama_service.py` | stdlib HTTP client for `/api/chat` (streaming NDJSON), `/api/tags`, `/api/version`, `/api/ps`, `/api/pull`; socket-level cancellation; error mapping |
| Chat flow | `green/ui/chat_controller.py` → `green/services/chat_engine.py` | user message persisted → background worker → engine builds context, streams, runs a tool loop (max rounds configurable), persists the reply |
| Tool registry | `green/services/tool_registry.py` | deny-by-default, Pydantic argument schemas (`extra="forbid"`), policy `prepare()`, confirmation callback, audit record |
| Permissions | `green/services/permission_service.py` | per-tool enable flag in config; `AuditLog` table |
| Tools | `green/tools/` | get_datetime, get_app_info, list_directory, search_files, read_text_file, create_text_file, open_application, open_website, save_memory, create_reminder, workspace_* , propose_file_change |
| Persistence | `green/database/` | SQLite schema v1, migrations with backup |

## Defects and gaps found

| # | Defect | Impact |
| --- | --- | --- |
| D1 | The approval dialog runs with `QDialog.exec()` (modal, nested event loop) from the worker bridge. While it is open the main window is blocked, so **Stop cannot be pressed**, and a nested event loop can re-enter other slots. | Approval cannot be cancelled from the chat; violates "approval dialogs must not freeze the application". |
| D2 | No cancellation check between approval and execution. If the user presses Stop just after approving, the tool still runs. | Cancelled work can still have side effects. |
| D3 | The model's tool-calling capability is never checked. Tools are always sent; the only fallback is matching the error text "does not support tools". | A model without tool support may silently ignore tools or fail; the user is not told. |
| D4 | Tool results have no machine-readable error code; the UI only shows a status line "Using tool: name" and, afterwards, tool names. There is no description of what is being attempted (which file, which URL) and no per-step running/succeeded/failed state. | Users cannot see what GREEN is doing or whether it worked. |
| D5 | `create_text_file` can never overwrite, even with explicit user approval. | Stage 2 requires "reject overwrite unless the user explicitly confirms". |
| D6 | `open_website` rejects every URL not on the allow-list; there is no confirmation path for other sites, and suspicious hosts (IP literals, punycode, unusual ports) are not flagged. | Tool is less useful than required; ambiguity is not surfaced. |
| D7 | No `get_system_info` tool (Windows version, Python, GREEN version, Ollama connectivity, model). | Missing required tool. |
| D8 | Tool history is a tab inside Tools & Permissions, refreshed only when the tab is opened; records have no result summary, duration or conversation link. | Hard to inspect what happened. |
| D9 | Permission categories are `read` / `controlled_write` / `restricted`; Stage 2 specifies READ / CONTROLLED / RESTRICTED. | Naming mismatch. |
| D10 | `PermissionError` / missing folders surface as a generic "File or system error". | Unclear error messages. |
| D11 | When the tool-step limit is reached the loop stops without letting the model use the results it already has. | Poorer final answers; user sees only a limit notice. |
| D12 | Persisted message metadata stores tool name/decision only, so after a restart the tool outcome summaries are lost. | History in the chat is incomplete. |

## Environment limitation

The sandbox used for this work cannot reach `ollama.com` or `registry.ollama.ai` (blocked by the network
policy), so Ollama and the qwen3 models cannot be installed here. Tool calling is therefore verified against an
in-process fake that implements Ollama's documented `/api/chat` tool-call format and `/api/show` capabilities,
and must be confirmed manually with a real model.
