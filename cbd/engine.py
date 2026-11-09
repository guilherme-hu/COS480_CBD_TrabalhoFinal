"""Engine: liga disco simulado, buffer pool e catálogo. Camada 1, dono: P6.

Implementa: arquitetura §8.1 (assinaturas exatas). Usa: FileManager, BufferPool
(P1) e Catalog (P2), importados só no construtor para que a preparação lógica da
base rode antes de essas camadas existirem.
Invariantes: `cold()` deixa o cache vazio e TODOS os contadores em zero; as
gravações de páginas sujas feitas pelo flush de `cold()` não entram na medição
seguinte. Uma base é reaberta com outra BufferConfig sem ser gerada de novo; trocar
o block_size exige outra pasta (root_dir).
Custo: o Engine não lê nem grava blocos por conta própria; `cold()` só grava as
páginas sujas que estiverem no buffer.
"""
from cbd.contracts import PageType


class Engine:
    def __init__(self, scfg, bcfg):
        """Abre (ou cria) a base em `scfg.root_dir` com blocos de `scfg.block_size` bytes."""
        # Importação adiada para permitir preparar dados antes das outras camadas.
        from cbd.storage.file_manager import FileManager
        from cbd.storage.buffer_pool import BufferPool
        from cbd.files.catalog import Catalog
        self.scfg, self.bcfg = scfg, bcfg
        self.fm = FileManager(scfg)
        self.bp = BufferPool(self.fm, bcfg)
        self.catalog = Catalog(self.bp)

    @property
    def M(self):
        """Memória de trabalho dos operadores, em páginas: frames da área WORK (§3.4)."""
        return self.bp.capacity(PageType.WORK)

    def cold(self):
        """Cache frio e contadores zerados: chamar imediatamente antes de cada medição.

        A ordem importa: `bp.clear()` grava as páginas sujas (flush_all) e só depois
        os contadores são zerados, para que essas gravações não sejam atribuídas à
        operação medida em seguida.
        """
        self.bp.clear()
        self.fm.reset_stats()
        self.bp.reset_stats()

    def snapshot(self):
        """Contadores atuais como dict plano, com os nomes de coluna do CSV (P6 §6.3).

        Chaves: `io.*` (FileManager), `bp.*` (global) e `bp.<AREA>.requests|hits|misses`
        por área. Não lê blocos.
        """
        out = {}
        for prefix, counters, names in (
            ('io', self.fm.io, ('reads', 'writes', 'seeks', 'seq_reads', 'rand_reads', 'seq_writes', 'rand_writes')),
            ('bp', self.bp.stats, ('requests', 'hits', 'misses', 'hit_rate', 'dirty_evictions', 'forced_writes', 'prefetched')),
        ):
            out.update({f'{prefix}.{name}': getattr(counters, name) for name in names})
        for area, counter in self.bp.stats_by_area.items():
            for name in ('requests', 'hits', 'misses'):
                out[f'bp.{area.value}.{name}'] = getattr(counter, name)
        return out

    def close(self):
        """Fecha o catálogo, grava as páginas sujas e fecha os arquivos, nesta ordem."""
        self.catalog.close()
        self.bp.flush_all()
        self.fm.close_all()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()
