"""API keys for online model services, kept in the operating system's credential store.

On Windows this is Windows Credential Manager (via the ``keyring`` package). Keys are
never written to GREEN's settings file, database or logs.
"""

from __future__ import annotations

import logging

log = logging.getLogger(__name__)

SERVICE = "GREEN"


class SecretStoreError(Exception):
    """User-presentable error."""


class SecretStore:
    def __init__(self, backend=None):
        self._backend = backend

    def _keyring(self):
        if self._backend is None:
            try:
                import keyring  # noqa: PLC0415
            except ImportError as exc:
                raise SecretStoreError("The 'keyring' package is not installed (pip install -r requirements.txt).") from exc
            self._backend = keyring
        return self._backend

    def available(self) -> tuple[bool, str]:
        try:
            backend = self._keyring()
            backend.get_password(SERVICE, "__probe__")
        except SecretStoreError as exc:
            return False, str(exc)
        except Exception as exc:  # e.g. keyring.errors.NoKeyringError on systems without a vault
            return False, f"No secure credential store is available ({exc.__class__.__name__})."
        return True, "Keys are stored in the system credential store."

    @staticmethod
    def _account(provider_id: str) -> str:
        return f"provider:{provider_id}"

    def get(self, provider_id: str) -> str | None:
        try:
            return self._keyring().get_password(SERVICE, self._account(provider_id))
        except SecretStoreError:
            return None
        except Exception:
            log.warning("Could not read an API key from the credential store")
            return None

    def has(self, provider_id: str) -> bool:
        return bool(self.get(provider_id))

    def set(self, provider_id: str, key: str) -> None:
        key = (key or "").strip()
        if not key:
            raise SecretStoreError("The API key is empty.")
        try:
            self._keyring().set_password(SERVICE, self._account(provider_id), key)
        except SecretStoreError:
            raise
        except Exception as exc:
            raise SecretStoreError(f"Could not save the key in the credential store ({exc.__class__.__name__}).") from exc

    def delete(self, provider_id: str) -> None:
        try:
            self._keyring().delete_password(SERVICE, self._account(provider_id))
        except Exception:
            pass  # nothing stored
