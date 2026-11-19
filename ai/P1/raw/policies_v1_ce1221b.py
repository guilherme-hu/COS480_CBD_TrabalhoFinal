"""Políticas de substituição de página do Buffer Pool (Camada 4 — P1).

Implementa as duas políticas escolhidas pelo grupo para comparação:
- LRU (Least Recently Used)
- MRU (Most Recently Used)

Garante que frames fixados (pin_count > 0) nunca sejam escolhidos como vítimas.
"""
from __future__ import annotations

from collections import OrderedDict
from typing import Any, Protocol

from cbd.contracts import Policy


class ReplacementPolicy(Protocol):
    def loaded(self, f: Any) -> None: ...
    def accessed(self, f: Any) -> None: ...
    def unpinned(self, f: Any) -> None: ...
    def removed(self, f: Any) -> None: ...
    def victim(self) -> Any | None: ...
    def reset(self) -> None: ...


class LRUPolicy:
    """Least Recently Used (medido pelo último acesso/pin, conforme SKS §13.5)."""

    def __init__(self) -> None:
        self._order: OrderedDict[int, Any] = OrderedDict()

    def loaded(self, f: Any) -> None:
        self._order[f.idx] = f
        self._order.move_to_end(f.idx)

    def accessed(self, f: Any) -> None:
        if f.idx in self._order:
            self._order.move_to_end(f.idx)
        else:
            self._order[f.idx] = f

    def unpinned(self, f: Any) -> None:
        pass

    def removed(self, f: Any) -> None:
        self._order.pop(f.idx, None)

    def victim(self) -> Any | None:
        for f in self._order.values():
            if f.pin_count == 0:
                return f
        return None

    def reset(self) -> None:
        self._order.clear()


class MRUPolicy:
    """Most Recently Used (escolhe o frame não fixado acessado mais recentemente)."""

    def __init__(self) -> None:
        self._order: OrderedDict[int, Any] = OrderedDict()

    def loaded(self, f: Any) -> None:
        self._order[f.idx] = f
        self._order.move_to_end(f.idx)

    def accessed(self, f: Any) -> None:
        if f.idx in self._order:
            self._order.move_to_end(f.idx)
        else:
            self._order[f.idx] = f

    def unpinned(self, f: Any) -> None:
        pass

    def removed(self, f: Any) -> None:
        self._order.pop(f.idx, None)

    def victim(self) -> Any | None:
        for f in reversed(self._order.values()):
            if f.pin_count == 0:
                return f
        return None

    def reset(self) -> None:
        self._order.clear()


def make_policy(policy: Policy, frames: list[Any] | None = None) -> ReplacementPolicy:
    if policy == Policy.LRU:
        return LRUPolicy()
    if policy == Policy.MRU:
        return MRUPolicy()
    raise ValueError(
        f"Política {policy} não adotada no projeto (apenas LRU e MRU foram selecionadas)."
    )
