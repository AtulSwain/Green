"""Builds a diagnostics report from real checks only (nothing is assumed)."""

from __future__ import annotations

import platform
import sqlite3
import sys
from dataclasses import dataclass, field

from .. import __version__


@dataclass
class DiagnosticItem:
    label: str
    value: str
    ok: bool | None = None  # None = informational


@dataclass
class DiagnosticsReport:
    items: list[DiagnosticItem] = field(default_factory=list)
    recent_errors: list[str] = field(default_factory=list)

    def add(self, label: str, value: str, ok: bool | None = None) -> None:
        self.items.append(DiagnosticItem(label, value, ok))

    def as_text(self) -> str:
        lines = ["GREEN diagnostics report", ""]
        for item in self.items:
            mark = "" if item.ok is None else ("[ok] " if item.ok else "[!!] ")
            lines.append(f"{mark}{item.label}: {item.value}")
        lines.append("")
        lines.append("Recent warnings and errors:")
        lines.extend(self.recent_errors or ["(none)"])
        return "\n".join(lines)


def _bytes(n: int) -> str:
    size = float(n)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if size < 1024 or unit == "TB":
            return f"{size:.1f} {unit}"
        size /= 1024
    return f"{n} B"


def build_report(services) -> DiagnosticsReport:
    """``services`` is an :class:`green.app.bootstrap.AppServices`."""
    report = DiagnosticsReport()
    config = services.config_store.get()
    report.add("GREEN version", __version__)
    report.add("Python", f"{platform.python_version()} ({sys.executable})")
    report.add("Operating system", f"{platform.system()} {platform.release()} ({platform.machine()})")
    try:
        from PySide6 import __version__ as pyside_version  # noqa: PLC0415
    except ImportError:
        pyside_version = "not installed"
    report.add("PySide6", pyside_version)
    report.add("SQLite", sqlite3.sqlite_version)

    status = services.ollama.status()
    report.add("Ollama URL", status.base_url + ("" if status.is_local else "  (REMOTE — conversation content leaves this computer)"),
               status.is_local)
    report.add("Ollama reachable", f"yes (version {status.version})" if status.reachable else f"no — {status.error}",
               status.reachable)
    report.add("Configured model", status.model)
    if status.reachable:
        report.add("Model installed", "yes" if status.model_installed else f"no — run: ollama pull {status.model}",
                   status.model_installed)
        names = ", ".join(m.name for m in status.installed_models) or "(none)"
        report.add("Installed models", names)
        try:
            running = services.ollama.running_models()
            if running:
                desc = "; ".join(
                    f"{m.name}: {_bytes(m.size_bytes)} total, {_bytes(m.size_vram_bytes)} in GPU memory (reported by Ollama)"
                    for m in running
                )
            else:
                desc = "no model currently loaded"
            report.add("Loaded models", desc)
        except Exception as exc:  # informational only
            report.add("Loaded models", f"unavailable ({exc.__class__.__name__})")
    report.add("GPU / VRAM", "not probed by GREEN; see 'Loaded models' for what Ollama reports")

    db = services.database
    try:
        integrity = db.query_one("PRAGMA quick_check")[0]
        report.add("Database", f"{db.path} (schema {db.schema_version()}, integrity {integrity})", integrity == "ok")
    except sqlite3.Error as exc:
        report.add("Database", f"{db.path} — error: {exc}", False)
    report.add("Settings file", str(services.paths.config_file))
    report.add("Logs", str(services.paths.log_dir))

    if config.voice.enabled:
        stt = services.voice_input.check_engine()
        mic = services.voice_input.check_microphone()
        tts = services.voice_output.check()
        report.add("Speech recognition", stt.detail, stt.available)
        report.add("Microphone", mic.detail, mic.available)
        report.add("Speech output", tts.detail, tts.available)
    else:
        report.add("Voice", "disabled in Settings")

    enabled = sorted(n for n, on in config.tools.enabled_tools.items() if on)
    report.add("Enabled tools", ", ".join(enabled) or "(none)")
    report.add("Approved directories", str(len(config.tools.approved_directories)))
    report.recent_errors = list(services.recent_errors.records) if services.recent_errors else []
    return report
