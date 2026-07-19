"""Explicit geometry sources used by WoodCAM operations.

The editor must not silently depend on the current FreeCAD selection.  This
module provides a tiny, dependency-free protocol and a router that can be used
by ``ui.py`` for the editor, the legacy selection and PanelNest alike.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Dict, Mapping, Optional


GeometryDict = Dict[str, list]


def empty_geometry() -> GeometryDict:
    return {"contours": [], "holes": []}


def normalize_geometry(value: Optional[Mapping[str, Any]]) -> GeometryDict:
    value = value or {}
    return {
        "contours": list(value.get("contours", []) or []),
        "holes": list(value.get("holes", []) or []),
    }


class GeometryProvider:
    """Small runtime protocol; subclasses may depend on FreeCAD or the editor."""

    provider_id = "unknown"

    def get_geometry(self) -> GeometryDict:
        raise NotImplementedError

    def describe_source(self) -> str:
        return self.provider_id

    def revision_token(self) -> str:
        return ""

    def has_geometry(self) -> bool:
        geometry = self.get_geometry()
        return bool(geometry["contours"] or geometry["holes"])


@dataclass
class CallableGeometryProvider(GeometryProvider):
    callback: Callable[[], Mapping[str, Any]]
    label: str
    provider_id: str = "callable"
    revision_callback: Optional[Callable[[], Any]] = None

    def get_geometry(self) -> GeometryDict:
        return normalize_geometry(self.callback())

    def describe_source(self) -> str:
        return self.label

    def revision_token(self) -> str:
        if self.revision_callback is None:
            return ""
        return str(self.revision_callback())


class GeometryProviderRouter:
    """Registry with an explicit active source and a safe fallback provider."""

    def __init__(self, fallback: Optional[GeometryProvider] = None) -> None:
        self._providers: Dict[str, GeometryProvider] = {}
        self._fallback = fallback
        self._active_id: Optional[str] = None

    @property
    def active_id(self) -> Optional[str]:
        return self._active_id

    def register(self, provider: GeometryProvider) -> None:
        provider_id = str(provider.provider_id)
        if not provider_id:
            raise ValueError("GeometryProvider precisa de provider_id.")
        self._providers[provider_id] = provider

    def activate(self, provider_id: Optional[str]) -> None:
        if provider_id is None:
            self._active_id = None
            return
        provider_id = str(provider_id)
        if provider_id not in self._providers:
            raise KeyError(provider_id)
        self._active_id = provider_id

    def current(self) -> GeometryProvider:
        if self._active_id is not None:
            return self._providers[self._active_id]
        if self._fallback is None:
            raise RuntimeError("Nenhuma fonte de geometria foi configurada.")
        return self._fallback

    def get_geometry(self) -> GeometryDict:
        return self.current().get_geometry()

    def describe_source(self) -> str:
        return self.current().describe_source()

    def revision_token(self) -> str:
        return self.current().revision_token()


__all__ = [
    "CallableGeometryProvider",
    "GeometryDict",
    "GeometryProvider",
    "GeometryProviderRouter",
    "empty_geometry",
    "normalize_geometry",
]
