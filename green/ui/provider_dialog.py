"""Add or edit an additional model service (OpenAI-compatible API)."""

from __future__ import annotations

import re

from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QVBoxLayout,
)
from pydantic import ValidationError

from ..config import ProviderSettings, format_validation_error
from ..services.ollama_service import OllamaError
from .workers import TaskRunner

# Addresses of well-known OpenAI-compatible services. Users can edit them.
PRESETS = (
    ("Moonshot AI (Kimi)", "https://api.moonshot.ai/v1"),
    ("OpenRouter (many models)", "https://openrouter.ai/api/v1"),
    ("SiliconFlow", "https://api.siliconflow.com/v1"),
    ("Groq", "https://api.groq.com/openai/v1"),
    ("Together AI", "https://api.together.xyz/v1"),
    ("LM Studio (on this computer)", "http://127.0.0.1:1234/v1"),
    ("Custom", ""),
)


def slugify(name: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")[:32].strip("-")
    return slug or "provider"


class ProviderDialog(QDialog):
    def __init__(self, parent, runner: TaskRunner, router, secrets, existing: ProviderSettings | None = None,
                 taken_ids: set[str] = frozenset()):
        super().__init__(parent)
        self.setWindowTitle("Edit online model service" if existing else "Add a model service")
        self.setMinimumWidth(560)
        self.runner = runner
        self.router = router
        self.secrets = secrets
        self.existing = existing
        self.taken_ids = set(taken_ids)
        self.result_provider: ProviderSettings | None = None
        self.result_key: str = ""

        layout = QVBoxLayout(self)
        form = QFormLayout()
        self.preset = QComboBox()
        for label, url in PRESETS:
            self.preset.addItem(label, url)
        self.preset.activated.connect(self._apply_preset)
        if existing is None:
            form.addRow("Service", self.preset)
        self.name = QLineEdit(existing.name if existing else "")
        self.name.setMaxLength(60)
        form.addRow("Name", self.name)
        self.base_url = QLineEdit(existing.base_url if existing else "")
        self.base_url.setPlaceholderText("https://…/v1")
        form.addRow("Address", self.base_url)
        self.api_key = QLineEdit()
        self.api_key.setEchoMode(QLineEdit.EchoMode.Password)
        has_key = existing is not None and secrets.has(existing.id)
        self.api_key.setPlaceholderText("Saved — leave empty to keep it" if has_key else "Paste your API key")
        form.addRow("API key", self.api_key)
        self.models = QLineEdit(", ".join(existing.models) if existing else "")
        self.models.setPlaceholderText("Model names, separated by commas (e.g. kimi-k3)")
        form.addRow("Models", self.models)
        fetch_row = QHBoxLayout()
        self.fetch_button = QPushButton("Fetch model list")
        self.fetch_button.setToolTip("Ask the service which models are available (uses the key above)")
        self.fetch_button.clicked.connect(self._fetch)
        self.available = QComboBox()
        self.available.setMinimumWidth(220)
        self.available.setEnabled(False)
        add_model = QPushButton("Add")
        add_model.clicked.connect(self._add_selected)
        fetch_row.addWidget(self.fetch_button)
        fetch_row.addWidget(self.available, 1)
        fetch_row.addWidget(add_model)
        form.addRow("", fetch_row)
        self.fetch_status = QLabel()
        self.fetch_status.setObjectName("Muted")
        self.fetch_status.setWordWrap(True)
        form.addRow("", self.fetch_status)
        layout.addLayout(form)

        note = QLabel(
            "Privacy: when you choose one of these models, your messages, relevant memories and tool results are "
            "sent to this service. Your API key is kept in the Windows Credential Manager, never in GREEN's files."
        )
        note.setObjectName("WarningText")
        note.setWordWrap(True)
        layout.addWidget(note)
        self.error = QLabel()
        self.error.setObjectName("ErrorText")
        self.error.setWordWrap(True)
        layout.addWidget(self.error)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self._accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        if existing is None:
            self._apply_preset()

    def _apply_preset(self, *_args) -> None:
        url = self.preset.currentData()
        label = self.preset.currentText()
        if url:
            self.base_url.setText(url)
        if not self.name.text().strip() or self.name.text() in [p[0] for p in PRESETS]:
            self.name.setText("" if label == "Custom" else label.split(" (")[0])

    def build(self) -> ProviderSettings:
        """Validated settings from the form. Raises ValidationError."""
        models = [m.strip() for m in self.models.text().split(",") if m.strip()]
        provider_id = self.existing.id if self.existing else self._new_id()
        current = self.existing.model if self.existing and self.existing.model in models else (models[0] if models else "")
        return ProviderSettings(
            id=provider_id,
            name=self.name.text().strip() or "Model service",
            base_url=self.base_url.text().strip(),
            models=models,
            model=current,
            consent_given=bool(self.existing and self.existing.consent_given
                               and self.existing.base_url == self.base_url.text().strip().rstrip("/")),
        )

    def _new_id(self) -> str:
        base = slugify(self.name.text())
        candidate, n = base, 2
        while candidate in self.taken_ids or candidate == "ollama":
            candidate = f"{base[:28]}-{n}"
            n += 1
        return candidate

    def _key_for_request(self) -> str | None:
        typed = self.api_key.text().strip()
        if typed:
            return typed
        return self.secrets.get(self.existing.id) if self.existing else None

    def _fetch(self) -> None:
        try:
            provider = self.build()
        except ValidationError as exc:
            self.fetch_status.setText(format_validation_error(exc))
            return
        backend = self.router.backend_for(provider, self._key_for_request())
        self.fetch_button.setEnabled(False)
        self.fetch_status.setText("Contacting the service…")

        def done(models):
            self.fetch_button.setEnabled(True)
            self.available.clear()
            for info in sorted(models, key=lambda m: m.name.lower()):
                self.available.addItem(info.name)
            self.available.setEnabled(bool(models))
            self.fetch_status.setText(f"{len(models)} model(s) available. Pick one and click Add."
                                      if models else "The service returned no models.")

        def failed(exc):
            self.fetch_button.setEnabled(True)
            self.fetch_status.setText(str(exc) if isinstance(exc, OllamaError) else f"Failed: {exc.__class__.__name__}")

        self.runner.submit(backend.list_models, on_done=done, on_error=failed)

    def _add_selected(self) -> None:
        name = self.available.currentText().strip()
        if not name:
            return
        models = [m.strip() for m in self.models.text().split(",") if m.strip()]
        if name not in models:
            models.append(name)
        self.models.setText(", ".join(models))

    def _accept(self) -> None:
        try:
            provider = self.build()
        except ValidationError as exc:
            self.error.setText(format_validation_error(exc))
            return
        if not provider.models:
            self.error.setText("Add at least one model name (type it, or use Fetch model list).")
            return
        has_key = bool(self.api_key.text().strip()) or (self.existing is not None and self.secrets.has(self.existing.id))
        if not provider.is_local and not has_key:
            self.error.setText("An API key is required for online services.")
            return
        self.result_provider = provider
        self.result_key = self.api_key.text().strip()
        self.accept()
