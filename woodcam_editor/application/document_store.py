"""Persistence boundary for the WoodCAM vector document.

The application layer depends on this small interface instead of importing
FreeCAD directly.  Production persistence is implemented by
``FreeCADDocumentStore``; pure tests may provide an in-memory implementation.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any, Optional


VECTOR_DOCUMENT_FEATURE_NAME = "WoodCAM2D_VectorDocument"
VECTOR_DOCUMENT_GROUP_NAME = "WoodCAM2D_VectorDrawing"
VECTOR_DOCUMENT_GROUP_LABEL = "WoodCAM 2D — Desenho"


class DocumentStoreError(RuntimeError):
    """Base error for persistence failures that must not overwrite user data."""


class StoredDocumentCorruptError(DocumentStoreError):
    """Raised when stored JSON/checksum cannot be validated."""


@dataclass(frozen=True)
class StoredDocumentInfo:
    """Stable information returned after a successful persistence operation."""

    feature_name: str
    document_uuid: str
    schema_version: int
    revision: int
    checksum: str

    @property
    def revision_token(self) -> str:
        return f"{self.document_uuid}:{self.revision}:{self.checksum}"


class VectorDocumentStore(ABC):
    """Port used by commands/controllers to persist a ``VectorDocument``.

    Implementations must load into a temporary domain object and validate it
    before returning.  ``save`` must be atomic from the host application's
    point of view.  Selection, camera and other session state do not belong in
    this store.
    """

    @abstractmethod
    def exists(self) -> bool:
        """Return whether a persisted vector document exists."""

    @abstractmethod
    def load(self) -> Optional[Any]:
        """Load and validate the persisted document, or return ``None``."""

    @abstractmethod
    def save(
        self,
        vector_document: Any,
        *,
        transaction_label: str = "WoodCAM 2D — Salvar desenho",
        use_transaction: bool = True,
        source_metadata: Optional[dict] = None,
    ) -> StoredDocumentInfo:
        """Persist one complete document snapshot atomically."""

    @abstractmethod
    def refresh_derived_shape(self, vector_document: Any) -> None:
        """Rebuild the host-visible shape cache from domain geometry."""

    @abstractmethod
    def stored_info(self) -> Optional[StoredDocumentInfo]:
        """Return metadata without deserializing the complete document."""
