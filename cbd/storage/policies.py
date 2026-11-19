"""Políticas de substituição de página do Buffer Pool (Camada 4, dono: P1 — Vivian).

Implementa: ReplacementPolicy, LRUPolicy, MRUPolicy, make_policy. Usa: cbd/contracts.py.
Decisões: D-P1-08 (implementação de LRU e MRU; preterição de FIFO e CLOCK). Ver docs/decisoes/P1.md.
Invariantes: frames fixados (pin_count > 0) nunca são escolhidos como vítimas.
Custo esperado: O(1) para loaded/accessed/removed; O(k) no pior caso em victim() se houver k frames fixados.
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
    """Least Recently Used (medido pelo último acesso/pin, conforme SKS §13.5 — D-P1-08)."""

    def __init__(self) -> None:
        # OrderedDict mantém a ordem de acesso:
        # - início do dict = frame acessado há MAIS tempo (candidato a vítima do LRU)
        # - fim do dict    = frame acessado há MENOS tempo (mais recente)
        self._order: OrderedDict[int, Any] = OrderedDict()

    def loaded(self, f: Any) -> None:
        """Registra um frame recém-carregado no fim da fila LRU (mais recente). Custo de I/O: 0 blocos."""
        # Quando um bloco novo entra no frame, vai para o fim da fila (mais recente)
        self._order[f.idx] = f
        self._order.move_to_end(f.idx)

    def accessed(self, f: Any) -> None:
        """Promove um frame acessado (hit) para o fim da fila LRU (mais recente). Custo de I/O: 0 blocos."""
        # Em caso de hit no pin(), move o frame para o fim da fila (mais recente)
        if f.idx in self._order:
            self._order.move_to_end(f.idx)
        else:
            self._order[f.idx] = f

    def unpinned(self, f: Any) -> None:
        """Notifica desafixação do frame (sem efeito na ordem de acesso do LRU). Custo de I/O: 0 blocos."""
        pass

    def removed(self, f: Any) -> None:
        """Remove o frame da fila de substituição ao ser expulso ou descartado. Custo de I/O: 0 blocos."""
        self._order.pop(f.idx, None)

    def victim(self) -> Any | None:
        """Retorna o frame não fixado (pin_count == 0) acessado há mais tempo, ou None. Custo de I/O: 0 blocos."""
        # D-P1-08, D-P1-10, D-P1-12: percorre do início para o fim (do mais antigo para o mais novo),
        # preferindo frames já referenciados (prefetched=False) para que o prefetch de um cursor não
        # seja bloqueado por um único bloco prefetched=True na ponta mais antiga da fila.
        fallback = None
        for f in self._order.values():
            if f.pin_count == 0:
                if not getattr(f, "prefetched", False):
                    return f
                if fallback is None:
                    fallback = f
        return fallback

    def reset(self) -> None:
        """Limpa a fila de controle da política. Custo de I/O: 0 blocos."""
        self._order.clear()


class MRUPolicy:
    """Most Recently Used (escolhe o frame não fixado acessado mais recentemente — D-P1-08)."""

    def __init__(self) -> None:
        # Mesma fila ordenada por último acesso do LRU, mas a vítima sai da ponta oposta (fim).
        # Ideal para laço interno cíclico de Nested-Loop Join quando b_S > DATA (evita sequential flooding).
        self._order: OrderedDict[int, Any] = OrderedDict()

    def loaded(self, f: Any) -> None:
        """Registra um frame recém-carregado no fim da fila MRU (mais recente). Custo de I/O: 0 blocos."""
        self._order[f.idx] = f
        self._order.move_to_end(f.idx)

    def accessed(self, f: Any) -> None:
        """Move um frame acessado (hit) para o fim da fila MRU (mais recente). Custo de I/O: 0 blocos."""
        if f.idx in self._order:
            self._order.move_to_end(f.idx)
        else:
            self._order[f.idx] = f

    def unpinned(self, f: Any) -> None:
        """Notifica desafixação do frame (sem efeito na ordem de acesso do MRU). Custo de I/O: 0 blocos."""
        pass

    def removed(self, f: Any) -> None:
        """Remove o frame da fila de substituição ao ser expulso ou descartado. Custo de I/O: 0 blocos."""
        self._order.pop(f.idx, None)

    def victim(self) -> Any | None:
        """Retorna o frame não fixado (pin_count == 0) acessado mais recentemente, ou None. Custo de I/O: 0 blocos."""
        # D-P1-08, D-P1-10, D-P1-12: percorre do mais recente para o mais antigo,
        # preferindo frames já referenciados (prefetched=False) para que o MRU não
        # expulse blocos pré-lidos antes do primeiro pin().
        fallback = None
        for f in reversed(self._order.values()):
            if f.pin_count == 0:
                if not getattr(f, "prefetched", False):
                    return f
                if fallback is None:
                    fallback = f
        return fallback

    def reset(self) -> None:
        """Limpa a fila de controle da política. Custo de I/O: 0 blocos."""
        self._order.clear()


def make_policy(policy: Policy, frames: list[Any] | None = None) -> ReplacementPolicy:
    """Instancia a política de substituição de páginas para uma área do Buffer Pool (D-P1-08).

    Parâmetros:
        policy: enum Policy (LRU ou MRU).
        frames: lista opcional de frames da área (mantida por compatibilidade de assinatura).
    Retorna:
        Instância de LRUPolicy ou MRUPolicy.
    Levanta:
        ValueError: se policy não for Policy.LRU nem Policy.MRU (FIFO e CLOCK foram preteridas).
    Custo de I/O: 0 blocos.
    """
    # D-P1-08: apenas LRU e MRU foram adotadas pelo grupo (FIFO e CLOCK preteridas em docs/decisoes/P1.md)
    if policy == Policy.LRU:
        return LRUPolicy()
    if policy == Policy.MRU:
        return MRUPolicy()
    raise ValueError(
        f"Política {policy} não adotada no projeto (apenas LRU e MRU foram selecionadas)."
    )
