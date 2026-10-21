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
| Desktop app | Sidebar with conversations and navigation, chat screen, Memory, Reminders, Workspace, Tools & Permissions, Tool activity, Voice, Settings and Diagnostics screens; dark and light themes; empty, loading and error states; status bar with live Ollama/model status |
| Conversations | New, select, rename (F2), delete (Del), search (titles and message text), automatic titles, copy message, stop generation (Esc), retry after an error, **Regenerate** the last reply, **Export** to Markdown, persisted in SQLite across restarts |
| Chat screen | Quick-start cards, code blocks with **Copy code**, message timestamps, **model switcher** in the header, measured speed under each reply (e.g. `qwen3:4b · 14.2 tok/s · 6.1 s`, as reported by Ollama) |
| Voice conversation | Click **Talk** (Ctrl+M), speak, and recording stops by itself when you pause; your words are sent and the answer is read aloud. Live microphone level, **Read aloud** on any reply, Stop speaking, mute. Spoken answers use the voices built into Windows (no download) |
| Practical extras | `calculate` tool (exact math, safe parser), "remind me in 10 minutes", tray icon with reminder notifications and optional keep-running-in-tray |
| Local model | Ollama connection and version check, installed-model check with `ollama pull` instructions, streaming responses, cancellation (interrupts even a blocked read), timeouts, out-of-memory and malformed-response handling, optional model download *only after you confirm* |
| Memory | Preferences, project notes, facts, instructions, conversation summaries; view, search, filter, add, edit, delete, clear all (with confirmation); "Remember…" on any message; "Summarize to memory" with review before saving; only relevant memories are sent with a request |
| Tool calling | Model ↔ tool loop over Ollama's native tool-calling API; checks the model actually supports tools; validates every call; per-call approval for controlled actions (never remembered); live step display ("Reading file …", ✓/✗ with the real result); step limit with a final no-tools answer; Stop works before, during approval and between steps |
| Tools | Deny-by-default registry, strict argument schemas, READ / CONTROLLED / RESTRICTED permissions, path canonicalization and containment, protected-file exclusion, non-blocking approval dialogs, Tool activity history |
| Developer assistance | Choose a project folder; browse, filter, preview and search files; "Ask GREEN about this file"; model tools to list/read/search the project; proposed edits are shown as a coloured diff and applied only after approval, with a backup of the original |
| Reminders | Create, edit, delete, mark complete; due reminders pop up while GREEN is running; model can propose reminders (with confirmation) |
| Diagnostics | Version, Python, OS, PySide6, SQLite, Ollama reachability and version, configured model and whether it is installed, models currently loaded (as reported by Ollama), database integrity, voice availability, recent redacted warnings; copy report |
| Data safety | Schema-versioned migrations in transactions, backup before upgrading, unreadable databases moved aside (never deleted), databases from newer versions refused untouched, atomic settings writes, invalid settings preserved and replaced by defaults |

### Partially complete

| Area | State |
| --- | --- |
| Voice input | faster-whisper + sounddevice. The real packages install and load on Python 3.12, but recording and transcription were tested only with a fake microphone and fake model (no microphone or model download in the build environment). Needs testing on Windows. |
| Voice output | Windows voices through Qt Text-to-Speech, tested with Qt's mock speech engine; real Windows voices not yet heard. Piper voices supported as an option (tested with a fake voice). |
| Keyboard accessibility | Tab navigation, focus indicators, shortcuts (Ctrl+N, Ctrl+L, Ctrl+, , Esc, F2, Del) and safe dialog defaults. Not audited with a screen reader. |

### Not implemented (planned)

- Wake-word listening (openWakeWord). Nothing listens in the background; the Voice screen says so.
- Reminders while GREEN is closed (no Windows Task Scheduler or startup integration).
- Syntax colouring inside code blocks (code blocks are monospace with a Copy button, but not coloured).
- Hands-free continuous conversation (you click Talk for each question, by design).
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
2. Open **Tools & Permissions** to decide what the model may do. Enabled initially: the read-only
   `get_datetime`, `get_app_info`, `get_system_info` and `calculate`, plus `create_reminder` and `save_memory`
   (which only write to GREEN's own data and always ask first). File tools also need at least one approved folder (Folders tab).
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
| `ollama.keep_alive` | `30m` | How long Ollama keeps the model loaded |
| `context.history_messages` | `20` | Recent messages sent with each request |
| `context.memory_items` | `6` | Maximum relevant memories per request |
| `context.max_tool_rounds` | `4` | Maximum tool steps per response |
| `ui.theme` | `dark` | `dark` or `light` |
| `ui.font_point_size` | `10` | Text size |
| `ui.send_on_enter` | `true` | Enter sends; otherwise Ctrl+Enter |
| `ui.close_to_tray` | `false` | Closing the window keeps GREEN running in the tray |
| `tools.enabled_tools` | `get_datetime`, `get_app_info`, `get_system_info`, `calculate`, `create_reminder`, `save_memory` | Tool name → enabled |
| `tools.approved_directories` | `[]` | Absolute folders readable by the file tools |
| `tools.output_directory` | `""` | Where `create_text_file` may create new files |
| `tools.approved_applications` | `{}` | Name → absolute path of an `.exe` that `open_application` may start |
| `tools.allowed_websites` | `[]` | Host names (and their subdomains) `open_website` opens without asking (https only) |
| `tools.confirm_unlisted_websites` | `true` | Allow other sites after asking each time; `false` refuses them |
| `tools.max_read_bytes` | `200000` | Maximum bytes read from one file |
| `tools.max_results` | `200` | Maximum listing/search results |
| `workspace.project_directory` | `""` | Project folder for the developer tools |
| `voice.*` | Talk button off | Edited on the Voice screen; see [Voice setup](#voice-setup) |

## Voice setup

Voice is optional. Everything runs on your computer: the microphone is used only after you click **Talk**, and
audio is never saved or uploaded.

**Hearing GREEN** needs nothing extra on Windows — it uses the voices built into Windows. Click **Read aloud** on
any reply, or open the **Voice** screen and click **Test voice**. More voices: Windows Settings → Time & language →
Speech → Add voices.

**Talking to GREEN:**

1. In GREEN's PowerShell window (with `(.venv)` showing): `pip install -r requirements-voice.txt`
2. Restart GREEN and open the **Voice** screen.
3. Tick **Show the Talk button in the chat**.
4. Under *Speech recognition*, pick a model (`base.en` is recommended, about 145 MB; `tiny.en` is fastest), tick
   **Allow GREEN to download the speech model once**, and click **Prepare speech model**. Or choose a model
   folder you downloaded yourself (for example `Systran/faster-whisper-base.en`).
5. In the chat, click **Talk** (or press Ctrl+M), ask your question and pause. GREEN stops listening by itself,
   sends what you said and reads the answer aloud.

Options on the Voice screen: answer out loud only for spoken questions (default) or for every reply, pause
length, automatic stop on/off, voice and speed, mute.

*Optional Piper voices* (more natural, larger download): `pip install -r requirements-piper.txt`, download a
voice (`.onnx` plus `.onnx.json`, e.g. from `rhasspy/piper-voices`), then choose *Piper voice file* as the engine
on the Voice screen. Piper 1.3+ is GPL-3.0 licensed; check each voice's licence.

If something is missing, GREEN says what and keeps working as a text assistant. On Windows, also check
*Settings → Privacy & security → Microphone* allows desktop apps to use the microphone.

## Speed tips

The model runs on your own hardware, so speed depends on it. Each reply shows the measured speed.

- Switch to a smaller model with the switcher at the top of the chat: `ollama pull qwen3:4b` (about twice as fast
  as 8b) or `qwen3:1.7b` (much faster, less capable).
- Run `ollama ps` while GREEN answers: `100% GPU` is fast; `CPU` means the model does not fit in graphics memory.
- Settings → *Context length* 4096 instead of 8192 makes each request lighter.
- *Keep model loaded* now defaults to 30 minutes so the model does not reload between questions.
- Leave *reasoning mode* off unless you need it; it makes replies much slower.

## Tools and tool calling

GREEN uses Ollama's documented tool-calling API (`tools` in `/api/chat`, structured `tool_calls` in the reply).
Before offering tools GREEN asks Ollama whether the configured model supports them (`/api/show` →
`capabilities`). If the model does not, GREEN answers without tools and tells you; Diagnostics and the status bar
show the same. qwen3 models report tool support in current Ollama versions. Text in a reply that merely *looks*
like a tool call is never executed.

One reply works like this: the model sees the enabled tools → requests one or more calls → each call is checked
(registered name → enabled → arguments valid → policy) → controlled tools ask you → the tool runs → its real
result (or a structured error such as `permission_denied`, `policy_violation`, `declined_by_user`) goes back to
the model → repeat until the model answers. After `context.max_tool_rounds` steps (default 4) the model gets one
last turn without tools and must answer with what it has. At most 5 calls run per step.

| Tool | Permission | Asks you? | What it does |
| --- | --- | --- | --- |
| `get_datetime` | Read | No | Local date, time and time zone |
| `calculate` | Read | No | Exact arithmetic (+ − × ÷, powers, sqrt, sin, log, …) with a safe parser — no code execution |
| `get_app_info` | Read | No | GREEN's non-sensitive settings (model, approved folders, enabled tools) |
| `get_system_info` | Read | No | OS version (e.g. "Windows 11 (build 22631)"), Python, GREEN version, Ollama reachability, model and tool support. No user name, host name, environment variables or paths |
| `list_directory` | Read | No | List a folder inside approved folders (result limit) |
| `search_files` | Read | No | Search file names inside approved folders, skipping protected folders |
| `read_text_file` | Read | No | Read a text/source file inside approved folders, truncated at `tools.max_read_bytes` |
| `workspace_list_files`, `workspace_read_file`, `workspace_search_text` | Read | No | Same for the selected project folder |
| `create_text_file` | Controlled | Every time | Create a file in the output folder. An existing file is replaced only if the model passes `overwrite=true` **and** you approve an "Overwrite existing file?" dialog showing the diff; a backup is kept |
| `open_application` | Controlled | Every time | Start an approved `.exe` by its approved name; no arguments, no shell; reports a program that exits with an error immediately |
| `open_website` | Controlled | When needed | Opens http(s) links only. Allow-listed sites over https open directly; other sites (if allowed in settings), plain http, raw IP addresses, unusual ports and look-alike international domains always ask, with the reason shown |
| `save_memory`, `create_reminder` | Controlled | Every time | Store a memory / a reminder (at a time, or `in_minutes`) in GREEN |
| `propose_file_change` | Controlled | Every time | Show a diff for a project file and apply it only if approved (backup kept) |

Approval dialogs do not block GREEN: you can keep reading, and **Stop** withdraws a pending approval so the action
never runs. An approval covers that single call only.

**Tool activity** (sidebar) lists every request with time, tool, permission decision, real outcome, a short
summary and duration; you can filter to failures and clear the history. File contents and messages are not
stored there.

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
- **Controlled tools** (`create_text_file`, `propose_file_change`, `open_application`, `open_website`,
  `save_memory`, `create_reminder`) show a confirmation dialog with the exact target and content or diff (see the
  table above for when `open_website` asks). Enter defaults to *Cancel*. Files are only replaced after an explicit
  overwrite approval, with a backup. Applications must be approved `.exe` programs and are started without a shell
  and without arguments. Approvals are per call and are re-checked against the current settings.
- **Errors fail closed.** If a tool's own safety check fails unexpectedly, the call is denied; it never falls
  through to execution. Tools are fixed Python code: there is no `eval`, `exec`, shell or generated code.
- **Restricted capabilities do not exist as tools:** no shell/PowerShell/CMD/Python execution, no credential or
  cookie access, no unrestricted filesystem access, no software installation, no security-setting changes.
- **Prompt injection.** File contents and tool results are marked as untrusted data, and the system prompt tells
  the model not to follow instructions in them. This reduces but cannot eliminate the risk that a malicious
  document steers the model; the permission checks and confirmations above are what actually protect you.
- **Tool activity.** Every tool request is recorded with its decision, outcome, a redacted summary and
  duration. File contents and messages are not recorded.
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

Last run (Linux, Python 3.12.3, PySide6 6.12.0, offscreen): **254 passed, 3 skipped**. Two skips are
Windows-only checks (`.exe`-only application rule, NTFS alternate data streams); the third checks behaviour
without the voice packages, which were installed for this run.

### Verification status

Verified by automated tests: everything in the *Complete* table, including that the UI event loop keeps running
during generation, that Stop and shutdown interrupt an active request, duplicate submissions are ignored,
unauthorized tool calls and file access are refused, and malformed model tool calls are handled. For tool
calling specifically (`tests/test_stage2_tools.py`, `tests/test_tool_loop.py`, `tests/test_gui_tools.py`): the
full request → validation → permission → approval → execution → result-to-model → final-answer loop, multiple
calls per step, structured errors returned to the model, the step limit, models without tool support, text that
only resembles a tool call, Ollama failures mid-loop, Stop before execution and during approval, non-blocking
approval dialogs, GUI responsiveness while a slow tool runs, overwrite protection, URL and application
allow-list rules, and the Tool activity history.

Still requires manual testing on Windows:

- A full conversation with a real Ollama model (qwen3:8b and qwen3:4b), including real tool calling: that the
  model chooses sensible tools, passes valid arguments, and uses the results. The sandbox used for development
  could not download Ollama or models, so tool calling was verified only against a fake server that follows
  Ollama's documented API format.
- Model download progress through *Download model…*.
- Talking with a real microphone and faster-whisper model (silence detection thresholds may need tuning for
  your microphone), and hearing replies with real Windows voices.
- The tray icon and reminder notifications on Windows (the build environment has no system tray).
- `open_application` and `open_website` launching real programs and the default browser.
- Display scaling, fonts and keyboard navigation on Windows 10/11.

## Troubleshooting

| Symptom | What to do |
| --- | --- |
| Status bar: *Ollama not reachable* | Start the Ollama app (or run `ollama serve`), then click **Check now**. Check the URL in Settings; the default is `http://127.0.0.1:11434`. |
| *Model … is not installed* | Run `ollama pull <model>` or pick an installed model under Settings → *Refresh list*. |
| *Not enough memory* | Choose a smaller model (e.g. `qwen3:4b`), reduce *Context length*, or close other programs. |
| Replies time out | The first reply loads the model and can be slow. Increase *Response timeout* in Settings. |
| The model never uses tools | Enable the tools in Tools & Permissions and approve folders. Check Diagnostics → *Tool calling*: if the model does not support tools, GREEN does not offer them; choose a model such as `qwen3:8b`. |
| An action shows ✗ in the chat | The step text says why (not permitted, invalid request, declined, or the real error). Tool activity has the full history. |
| A file tool says *outside the approved directories* or *protected* | Add the folder under Tools & Permissions → Folders. Protected files (keys, `.env`, `.git`, …) cannot be unlocked. |
| *Settings file was invalid* on start | Your previous file was kept as `config.json.invalid-<time>` next to the new one. |
| *Database could not be read and was moved aside* | The old file was kept as `green.db.corrupt-<time>`. You can inspect it with any SQLite tool. |
| *Database was created by a newer version of GREEN* | Use the newer GREEN version; the file was not modified. |
| Voice: *not installed* | `pip install -r requirements-voice.txt` in GREEN's PowerShell window (with `(.venv)` showing), then restart GREEN. |
| Voice: recording never stops by itself | Your room may be noisy for the default sensitivity; click **Talk** again to stop, or turn off automatic stop on the Voice screen. |
| No spoken answer | Check the Voice screen status and click **Test voice**. Make sure *Mute* is off and Windows has a voice installed. |
| Replies are slow | See [Speed tips](#speed-tips). |
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
