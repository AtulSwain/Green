# GREEN 2.0.1

GREEN is a local-first personal AI assistant for Windows. It is a native desktop app (PySide6) that talks to a
language model running on your own computer through [Ollama](https://ollama.com), keeps conversations and
memories in a local SQLite database, and can act on your computer only through a small set of tools that you
enable and approve.

By default nothing leaves your computer: no cloud AI, no analytics, no telemetry.

> **Status:** 2.0.1 is the first release in this repository (there is no earlier 2.0.0 release). Everything listed
> as complete below is covered by automated tests that run without Ollama, a microphone or Windows, using an
> in-process fake Ollama server. It has **not** yet been run on a Windows machine against a real Ollama model,
> microphone or Piper voice. See [Verification status](#verification-status).

---

## Feature status

### Complete (implemented and covered by tests)

| Area | What works |
| --- | --- |
| Desktop app | Sidebar with conversations and navigation, chat screen, Memory, Reminders, Workspace, Tools & Permissions, Voice, Settings and Diagnostics screens; dark and light themes; empty, loading and error states; status bar with live Ollama/model status |
| Conversations | New, select, rename (F2), delete (Del), automatic titles, copy message, stop generation (Esc), retry after an error, persisted in SQLite across restarts |
| Local model | Ollama connection and version check, installed-model check with `ollama pull` instructions, streaming responses, cancellation (interrupts even a blocked read), timeouts, out-of-memory and malformed-response handling, optional model download *only after you confirm* |
| Memory | Preferences, project notes, facts, instructions, conversation summaries; view, search, filter, add, edit, delete, clear all (with confirmation); "Remember…" on any message; "Summarize to memory" with review before saving; only relevant memories are sent with a request |
| Tools | Deny-by-default registry, strict argument schemas, path canonicalization and containment, protected-file exclusion, confirmation dialogs for anything that changes something, audit log |
| Developer assistance | Choose a project folder; browse, filter, preview and search files; "Ask GREEN about this file"; model tools to list/read/search the project; proposed edits are shown as a coloured diff and applied only after approval, with a backup of the original |
| Reminders | Create, edit, delete, mark complete; due reminders pop up while GREEN is running; model can propose reminders (with confirmation) |
| Diagnostics | Version, Python, OS, PySide6, SQLite, Ollama reachability and version, configured model and whether it is installed, models currently loaded (as reported by Ollama), database integrity, voice availability, recent redacted warnings; copy report |
| Data safety | Schema-versioned migrations in transactions, backup before upgrading, unreadable databases moved aside (never deleted), databases from newer versions refused untouched, atomic settings writes, invalid settings preserved and replaced by defaults |

### Partially complete

| Area | State |
| --- | --- |
| Voice input (push-to-talk) | Implemented with faster-whisper + sounddevice. Tested with fake audio devices and a fake model only. Needs testing with a real microphone on Windows. |
| Voice output | Implemented with Piper + sounddevice (supports the piper-tts 1.2 and 1.3+ APIs). Tested with a fake voice only. |
| Keyboard accessibility | Tab navigation, focus indicators, shortcuts (Ctrl+N, Ctrl+L, Ctrl+, , Esc, F2, Del) and safe dialog defaults. Not audited with a screen reader. |

### Not implemented (planned)

- Wake-word listening (openWakeWord). Nothing listens in the background; the Voice screen says so.
- Reminders while GREEN is closed (no Windows Task Scheduler or startup integration).
- System-tray icon / minimise to tray.
- Syntax-highlighted code blocks in chat (code is shown in a monospace font without highlighting).
- Encrypted database. Data is protected only by your Windows account.
- Packaged installer (.exe/.msi).

---

## Requirements

- Windows 10 or 11 (64-bit). GREEN also starts on Linux and macOS, which is how the tests run.
- Python 3.12 (3.11+ works).
- [Ollama](https://ollama.com/download) for the language model.
- Enough memory for your chosen model. As a rough guide `qwen3:8b` is about a 5 GB download and `qwen3:4b`
  about 2.5 GB; actual speed and memory use depend on your hardware and were not measured for this release.

## Installation

In PowerShell, from the folder containing this README:

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
pip install -r requirements.txt
```

Optional voice support:

```powershell
pip install -r requirements-voice.txt
```

### Install the model

Install and start Ollama, then download a model (GREEN never downloads one without asking):

```powershell
ollama pull qwen3:8b      # recommended
ollama pull qwen3:4b      # smaller alternative for computers with less memory
```

You can pick any installed model under **Settings → Local model**. *Refresh list* shows what Ollama has, and
*Download model…* asks Ollama to download the name in the box after you confirm.

## Running

```powershell
.\.venv\Scripts\Activate.ps1
python main.py
```

The status bar shows whether Ollama is reachable and the configured model is installed. If Ollama is not
running GREEN still opens; you can browse conversations, memories and reminders, and send messages once Ollama
is available.

### First steps

1. Send a message. Replies stream in; press **Stop** (or Esc) to cancel.
2. Open **Tools & Permissions** to decide what the model may do. Only `get_datetime` and `get_app_info` are
   enabled initially. File tools also need at least one approved folder (Folders tab).
3. Open **Workspace** to choose a project folder for code help.

## Where data is stored

| What | Location on Windows |
| --- | --- |
| Settings | `%APPDATA%\GREEN\config.json` |
| Database (conversations, memories, reminders, audit log) | `%LOCALAPPDATA%\GREEN\green.db` |
| Logs (rotating, redacted) | `%LOCALAPPDATA%\GREEN\logs\` |
| Database backups and backups of files changed by approved edits | `%LOCALAPPDATA%\GREEN\backups\` |

Set the environment variable `GREEN_HOME` to keep everything in one folder instead (for example a portable
install): settings go to `GREEN_HOME\config` and data to `GREEN_HOME\data`.

To back up GREEN, close it and copy the data folder. **Settings → Open data folder** opens it.

## Configuration

Everything is editable in the app (Settings, Tools & Permissions, Workspace, Voice). The settings file is plain
JSON and is validated on load; if it is invalid, GREEN keeps the bad file as `config.json.invalid-<time>` and
starts with defaults.

| Key | Default | Meaning |
| --- | --- | --- |
| `ollama.base_url` | `http://127.0.0.1:11434` | Ollama address. A non-local address triggers a warning because messages would leave your computer. Credentials in the URL are rejected. |
| `ollama.model` | `qwen3:8b` | Model name as shown by `ollama list` |
| `ollama.request_timeout_seconds` | `180` | Maximum wait for the next piece of a response (5–3600) |
| `ollama.connect_timeout_seconds` | `5` | Connection timeout |
| `ollama.temperature`, `ollama.top_p` | `0.7`, `0.9` | Sampling parameters |
| `ollama.num_ctx` | `8192` | Context length in tokens |
| `ollama.num_predict` | `-1` | Maximum response tokens (`-1` = model default) |
| `ollama.think` | `false` | Reasoning mode for models that support it |
| `ollama.keep_alive` | `5m` | How long Ollama keeps the model loaded |
| `context.history_messages` | `20` | Recent messages sent with each request |
| `context.memory_items` | `6` | Maximum relevant memories per request |
| `context.max_tool_rounds` | `4` | Maximum tool steps per response |
| `ui.theme` | `dark` | `dark` or `light` |
| `ui.font_point_size` | `10` | Text size |
| `ui.send_on_enter` | `true` | Enter sends; otherwise Ctrl+Enter |
| `tools.enabled_tools` | `get_datetime`, `get_app_info` | Tool name → enabled |
| `tools.approved_directories` | `[]` | Absolute folders readable by the file tools |
| `tools.output_directory` | `""` | Where `create_text_file` may create new files |
| `tools.approved_applications` | `{}` | Name → absolute path of an `.exe` that `open_application` may start |
| `tools.allowed_websites` | `[]` | Host names (and their subdomains) `open_website` may open |
| `tools.max_read_bytes` | `200000` | Maximum bytes read from one file |
| `tools.max_results` | `200` | Maximum listing/search results |
| `workspace.project_directory` | `""` | Project folder for the developer tools |
| `voice.*` | voice off | See [Voice setup](#voice-setup) |

## Voice setup

Voice is optional and off by default. Everything runs locally; audio is recorded only while you hold the
**Hold to talk** button and is never stored or uploaded.

1. `pip install -r requirements-voice.txt`
2. **Speech recognition (faster-whisper).** Either
   - download a converted model folder (for example `Systran/faster-whisper-base.en` from Hugging Face) and
     choose it under **Settings → Voice → Speech model → Folder…**, or
   - type a model name such as `base.en` and tick *Allow a one-time download* (about 75 MB for tiny models up to
     about 3 GB for large ones).
3. **Speech output (Piper).** Download a voice — both the `.onnx` file and its `.onnx.json` — for example from
   the `rhasspy/piper-voices` collection, and choose the `.onnx` under **Settings → Voice → Piper voice**.
   Check each voice's licence on its model card.
4. Tick **Enable voice features** and save. The **Voice** screen shows what is available and lets you test
   speech output, mute, and turn spoken replies on or off.

If anything is missing, GREEN explains what and keeps working as a text assistant. On Windows, also check
*Settings → Privacy & security → Microphone* allows desktop apps to use the microphone.

## Security model

GREEN treats the language model as untrusted. The model can only *request* a tool; GREEN decides.

- **Deny by default.** Only tools you tick in Tools & Permissions are offered to the model, and every call is
  re-checked at execution time.
- **Read tools** (`list_directory`, `search_files`, `read_text_file`, `workspace_*`) work only inside approved
  folders or the selected project. Paths are canonicalized with links resolved, so `..`, absolute paths
  elsewhere, symbolic links or junctions pointing outside, UNC/device paths and NTFS alternate data streams are
  rejected. Protected names are always hidden, even inside approved folders: `.env*`, keys and certificates
  (`*.pem`, `*.key`, `id_rsa*`, …), credential files, `.git`, `.ssh`, `.aws`, `node_modules`, virtual
  environments, browser cookie and password stores. Reads are size-limited and binary files are refused.
- **Controlled write tools** (`create_text_file`, `propose_file_change`, `open_application`, `open_website`,
  `save_memory`, `create_reminder`) always show a confirmation dialog with the exact target and content or diff.
  Enter defaults to *Cancel*. New files are never overwritten; approved project edits keep a backup copy.
  Applications must be approved `.exe` programs and are started without a shell and without arguments.
  Websites must be on your allow-list.
- **Restricted capabilities do not exist as tools:** no shell/PowerShell/CMD/Python execution, no credential or
  cookie access, no unrestricted filesystem access, no software installation, no security-setting changes.
- **Prompt injection.** File contents and tool results are marked as untrusted data, and the system prompt tells
  the model not to follow instructions in them. This reduces but cannot eliminate the risk that a malicious
  document steers the model; the permission checks and confirmations above are what actually protect you.
- **Audit log.** Every tool request is recorded with its decision and outcome (Tools & Permissions → Audit log).
  File contents and messages are not recorded.
- **Logs** never contain conversation text, and API keys, tokens, passwords and private keys are redacted.
- **Links** in model replies are not clickable; use the `open_website` tool or copy the link.

### Known security limitations

- Data is stored unencrypted in your Windows profile; anyone with access to your account can read it.
- Ollama's local API has no authentication. Other programs on your computer can use it too. Do not expose
  Ollama to your network unless you understand the consequences.
- A model can still *suggest* harmful commands in text. GREEN cannot execute them, but you could copy and run
  them yourself — review generated code before using it.

## Privacy

| | Default behaviour |
| --- | --- |
| Model inference | Local Ollama at `127.0.0.1`. Changing the URL to another machine shows a warning before saving. |
| Storage | Local SQLite database and JSON settings in your user profile. |
| Internet | GREEN itself only contacts Ollama. Internet is used only when you click *Download model* (Ollama downloads it), allow a speech-model download, or approve opening a website. |
| Third-party services | None. No analytics, telemetry, crash reporting or advertising. |

## Testing

```powershell
pip install -r requirements-dev.txt
python -m pytest
```

On a machine without a display (CI, Linux servers) set `QT_QPA_PLATFORM=offscreen` first. The tests need no
Ollama, microphone, network or Windows: an in-process fake Ollama server (`tests/fake_ollama.py`) streams
responses, tool calls and errors over real HTTP, and voice tests use fake audio devices. Tests only touch
temporary folders.

Last run (Linux, Python 3.12.3, PySide6 6.12.0, offscreen): **138 passed, 2 skipped** — the two skips are
Windows-only checks (`.exe`-only application rule and NTFS alternate data streams).

### Verification status

Verified by automated tests: everything in the *Complete* table, including that the UI event loop keeps running
during generation, that Stop and shutdown interrupt an active request, duplicate submissions are ignored,
unauthorized tool calls and file access are refused, and malformed model tool calls are handled.

Still requires manual testing on Windows:

- A full conversation with a real Ollama model (qwen3:8b and qwen3:4b), including real tool calling.
- Model download progress through *Download model…*.
- Push-to-talk with a real microphone and faster-whisper model; Piper speech output.
- `open_application` and `open_website` launching real programs and the default browser.
- Display scaling, fonts and keyboard navigation on Windows 10/11.

## Troubleshooting

| Symptom | What to do |
| --- | --- |
| Status bar: *Ollama not reachable* | Start the Ollama app (or run `ollama serve`), then click **Check now**. Check the URL in Settings; the default is `http://127.0.0.1:11434`. |
| *Model … is not installed* | Run `ollama pull <model>` or pick an installed model under Settings → *Refresh list*. |
| *Not enough memory* | Choose a smaller model (e.g. `qwen3:4b`), reduce *Context length*, or close other programs. |
| Replies time out | The first reply loads the model and can be slow. Increase *Response timeout* in Settings. |
| The model never uses tools | Enable the tools in Tools & Permissions and approve folders. Some models do not support tool calling; GREEN then continues without tools. |
| A file tool says *outside the approved directories* or *protected* | Add the folder under Tools & Permissions → Folders. Protected files (keys, `.env`, `.git`, …) cannot be unlocked. |
| *Settings file was invalid* on start | Your previous file was kept as `config.json.invalid-<time>` next to the new one. |
| *Database could not be read and was moved aside* | The old file was kept as `green.db.corrupt-<time>`. You can inspect it with any SQLite tool. |
| *Database was created by a newer version of GREEN* | Use the newer GREEN version; the file was not modified. |
| Voice: *not installed* | `pip install -r requirements-voice.txt` in the same environment. |
| Voice: *not available locally* | Choose a downloaded model folder or allow the one-time download in Settings → Voice. |
| Voice: *No microphone available* | Connect a microphone and check Windows microphone privacy settings. |
| Anything else | Open **Diagnostics**, run checks, and look at `%LOCALAPPDATA%\GREEN\logs\green.log`. |

## Project layout

```
main.py                    Entry point (python main.py)
green/
  config.py                Validated settings (Pydantic) and atomic settings store
  paths.py                 Per-user data/config locations (GREEN_HOME override)
  logging_setup.py         Rotating, redacted logs; recent-errors buffer for diagnostics
  models.py                Data classes shared by services and UI
  app/bootstrap.py         Builds the service layer (no GUI)
  app/application.py       Qt application start-up and shutdown
  database/                SQLite connections (per thread) and versioned migrations
  security/                Path policy and secret redaction
  services/                Ollama client, chat engine, conversations, memory, reminders,
                           tool registry, permissions/audit, file access, voice, diagnostics
  tools/                   Built-in tools offered to the model
  ui/                      Main window, screens, dialogs, theme tokens, background workers
tests/                     pytest + pytest-qt suite and the fake Ollama server
```

Blocking work (model requests, file listing and search, diagnostics, voice) runs on a thread pool; results come
back to the GUI thread through queued Qt signals. Tool confirmations requested from a worker are shown on the GUI
thread while the worker waits, and are declined automatically if you stop the response.

## Third-party software

PySide6 (LGPL-3.0), Pydantic (MIT). Optional: faster-whisper (MIT), sounddevice (MIT), NumPy (BSD), piper-tts
(GPL-3.0 for 1.3+; MIT for earlier releases). Model weights and voices have their own licences; check them before
use.
