"""Selection state for the vector editor.

The selection belongs to the editor session, not to the persistent vector
document.  This small model intentionally has no Qt dependency so it can be
used by the controller and by pure tests.
"""

from __future__ import annotations

from typing import Callable, Iterable, Iterator, List, Optional, Sequence, Tuple


SelectionListener = Callable[[Tuple[str, ...]], None]


class SelectionModel:
    """Ordered, observable collection of selected entity identifiers."""

    def __init__(self, initial: Iterable[str] = ()) -> None:
        self._ids: List[str] = []
        self._listeners: List[SelectionListener] = []
        self.replace(initial)

    @property
    def ids(self) -> Tuple[str, ...]:
        return tuple(self._ids)

    @property
    def primary_id(self) -> Optional[str]:
        return self._ids[-1] if self._ids else None

    def __iter__(self) -> Iterator[str]:
        return iter(self._ids)

    def __len__(self) -> int:
        return len(self._ids)

    def __contains__(self, entity_id: object) -> bool:
        return entity_id in self._ids

    def subscribe(self, listener: SelectionListener) -> Callable[[], None]:
        if listener not in self._listeners:
            self._listeners.append(listener)

        def unsubscribe() -> None:
            if listener in self._listeners:
                self._listeners.remove(listener)

        return unsubscribe

    def clear(self) -> bool:
        return self.replace(())

    def select_only(self, entity_id: Optional[str]) -> bool:
        return self.replace(()) if entity_id is None else self.replace((entity_id,))

    def replace(self, entity_ids: Iterable[str]) -> bool:
        unique: List[str] = []
        seen = set()
        for entity_id in entity_ids:
            value = str(entity_id)
            if value not in seen:
                seen.add(value)
                unique.append(value)
        if unique == self._ids:
            return False
        self._ids = unique
        self._emit()
        return True

    def add(self, entity_id: str) -> bool:
        entity_id = str(entity_id)
        if entity_id in self._ids:
            return False
        self._ids.append(entity_id)
        self._emit()
        return True

    def remove(self, entity_id: str) -> bool:
        entity_id = str(entity_id)
        if entity_id not in self._ids:
            return False
        self._ids.remove(entity_id)
        self._emit()
        return True

    def toggle(self, entity_id: str) -> bool:
        return self.remove(entity_id) if entity_id in self else self.add(entity_id)

    def prune(self, valid_ids: Iterable[str]) -> bool:
        valid = set(str(value) for value in valid_ids)
        return self.replace(value for value in self._ids if value in valid)

    def _emit(self) -> None:
        snapshot = self.ids
        for listener in tuple(self._listeners):
            listener(snapshot)


__all__ = ["SelectionModel", "SelectionListener"]
