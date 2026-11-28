"""Operações parciais comuns da B+ (P3), sem metadados inventados.

SKS §14.3.1; D-P3-04/06/07. Helpers recebem contexto explicitamente, pois
bloco 1 e orçamento de construção ainda são L02/L04 do P3_PLANO_ATUAL.md §8.
Toda página é acessada por P1; não há alocação nem leitura direta de disco.
"""
from cbd.files.btree_node import _InternalPage
from cbd.records.slotted_page import SlottedPage


def _pending(gaps, behavior):
    """Falha explícita de desenvolvimento, não política final de API (L05)."""
    raise NotImplementedError(
        f"P3: {behavior}; pendente {gaps}. "
        "Ver docs/implementacao/P3_PLANO_ATUAL.md §8 e mapa de decisões: "
        "https://app.notion.com/p/4269ab021b6d81508de1d7317021236f"
    )


def _descend(bp, fid, ptype, root, height, key, separator_size):
    """Bloco da folha para uma chave completa, com raiz/altura fornecidas.

    Altura inclui folhas e deve ser positiva. Mantém no máximo 1 pin;
    custo frio = height−1 blocos internos; não lê a folha retornada.
    Não abre árvore nem valida metadados (L02); ptype é dado pelo chamador,
    não fixa a política de áreas ainda aberta em L04.
    """
    if height < 1 or root < 1 or len(key) != separator_size:
        raise ValueError("contexto de descida inválido")
    block = root
    for _ in range(height - 1):
        with bp.page(fid, block, ptype) as page:
            block = _InternalPage(page.data, separator_size).child_for(key)
    return block


def _fetch_primary(bp, fid, ptype, ptr, entries):
    """RID válido → registro, inclusive NOT UNIQUE; 1 página, no máximo 1 pin.

    D-P3-04/07. entries fornece perfil explicitamente, sem abrir bloco 1.
    Não define API de referência inválida: L05; slot apagado é recusado aqui
    com ValueError interno. Retorna tuple do codec, nunca view da página.
    """
    with bp.page(fid, ptr.block, ptype) as page:
        raw = SlottedPage(page.data).get(ptr.slot)
        if raw is None:
            raise ValueError("RID aponta para slot apagado")
        return entries.decode(raw)


def expected_height(n_entries: int, fanout: int, fill: float) -> int:
    """Esqueleto da assinatura existente; estimativa depende de L03/L05.

    Não assumir que n_entries significa folhas nem preencher com altura 0/1.
    """
    _pending("L03/L05", "fórmula de altura estimada e significado das entradas")
