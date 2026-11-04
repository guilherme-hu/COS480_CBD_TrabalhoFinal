"""Integração P6 conforme arquitetura §8.1; requer implementações P1/P2/P3."""
from cbd.contracts import PageType


class Engine:
    def __init__(self, scfg, bcfg):
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
        return self.bp.capacity(PageType.WORK)

    def cold(self):
        self.bp.clear()
        self.fm.reset_stats()
        self.bp.reset_stats()

    def snapshot(self):
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
        self.catalog.close()
        self.bp.flush_all()
        self.fm.close_all()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()
