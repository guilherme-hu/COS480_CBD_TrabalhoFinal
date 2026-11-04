# P1 — Camada 4: Gerente de Arquivos e Gerente de Buffer Pool

> Leia junto com [`00_ARQUITETURA_GERAL.md`](00_ARQUITETURA_GERAL.md). As assinaturas públicas estão lá (§3 e §4) e **não podem mudar** sem PR do grupo. Aqui está o *como* implementar.

**Peso:** ~17%. **Primeiro na fila:** P2 e P3 dependem de você. O marco de **05/11** é entregar `contracts.py`, o esqueleto do repositório e um `FileManager` + `BufferPool` mínimo funcionando (só LRU, uma área). O resto até **12/11**. Depois disso você coordena os **testes de estresse** e consolida o **`POST_MORTEM.md`**.

---

## Em resumo (leia primeiro)

- **Sua parte é a base de tudo:** um **disco de mentira** e um **cache de páginas** (buffer pool). Todo o resto do sistema lê e grava por você.
- **Disco simulado (`FileManager`):** cada tabela é um arquivo comum do Windows/Linux, dividido em blocos de tamanho fixo (ex.: 4 KB). Você **conta** cada bloco lido ou gravado e marca se foi **sequencial** (o bloco seguinte ao anterior) ou com **seek** (salto). Esses contadores são o resultado do trabalho.
- **Buffer pool:** guarda algumas páginas em memória. Se a página pedida já está lá é um **acerto** (hit, sem custo); se não, é uma **falta** (miss, lê do disco). Ele é dividido em **4 áreas**: catálogo, índices, dados e trabalho (a memória dos algoritmos).
- **Quando a área enche**, a **política de substituição** escolhe quem sai: LRU (o usado há mais tempo), MRU (o usado mais recentemente) ou Clock (segunda chance).
- **`pin`/`unpin`** querem dizer "estou usando esta página" e "larguei". **`dirty`** quer dizer "alterei, precisa gravar antes de tirar da memória".
- **Extras pedidos pelo enunciado:** toss-immediate, leitura antecipada (prefetch), gravação forçada e reserva de memória para os algoritmos.
- **Urgente:** o esqueleto do repositório, o `contracts.py` e uma versão mínima (só LRU) **até 05/11**. Todo mundo depende disso para começar.
- **Depois:** você coordena os testes de estresse e junta tudo no `POST_MORTEM.md`.
- **Sugestão para o grupo:** comparar LRU × MRU mostra muito mais diferença que LRU × Clock.

---

## 1. O que você entrega

| Arquivo | Conteúdo |
| :--- | :--- |
| `pyproject.toml` | nome do projeto, `requires-python = ">=3.11"`, config do pytest (`testpaths = ["tests"]`) |
| `cbd/__init__.py`, `cbd/storage/__init__.py`, `cbd/records/__init__.py`, `cbd/files/__init__.py`, `cbd/exec/__init__.py`, `bench/__init__.py` | pacotes vazios |
| `cbd/contracts.py` | **cópia exata** do §4 do documento geral |
| `cbd/storage/file_manager.py` | `FileManager` |
| `cbd/storage/buffer_pool.py` | `Page`/`Frame`, `BufferPool`, áreas |
| `cbd/storage/policies.py` | `LRU`, `MRU`, `Clock`, `FIFO` |
| `tests/conftest.py` | fixture `eng` (geral §11) + fixture `bp` só da camada 4 (abaixo) |
| `tests/test_file_manager.py`, `tests/test_buffer_pool.py`, `tests/test_policies.py` | testes da sua camada |
| `tests/stress/` | testes de estresse do sistema (§6) |
| `POST_MORTEM.md` | consolidação final (§7) |

Fixture extra para quem testa só a camada 4 (P2 e P3 podem usar antes do `Engine` existir):

```python
@pytest.fixture
def bp(tmp_path):
    from cbd.contracts import StorageConfig, BufferConfig
    from cbd.storage.file_manager import FileManager
    from cbd.storage.buffer_pool import BufferPool
    fm = FileManager(StorageConfig(root_dir=str(tmp_path)))
    pool = BufferPool(fm, BufferConfig())
    yield pool
    assert pool.pinned() == 0, "pin sem unpin"
    pool.flush_all()
    fm.close_all()
```

---

## 2. Gerente de Arquivos (`FileManager`)

### 2.1 Decisões (enunciado §2.1). Registre as escolhas finais no relatório

| Decisão | Escolha recomendada | Por quê |
| :--- | :--- | :--- |
| Tamanho do bloco | configurável (2048/4096/8192), padrão 4096 | é fator do experimento |
| Alocação | **contígua por extensão do arquivo** (append). Blocos liberados vão para uma lista de livres e são reusados antes de estender | scan de heap vira leitura sequencial de verdade; simples de contar |
| Descritor (cabeçalho) | bloco físico 0 de cada arquivo (layout abaixo) | um lugar só; fora da contagem |
| Blocos livres | pilha de `bno` livres guardada no cabeçalho (até `(B − 32) / 4` entradas; o excedente é esquecido e contado em `lost_free`) | sem I/O extra para alocar; limite raro de atingir |
| Contadores | leituras/gravações × sequencial/aleatório, por arquivo e global | o enunciado pede; o seek é derivado |

### 2.2 Arquivo no SO

- Caminho `<root_dir>/<name>.dat`. Crie `root_dir` se não existir.
- O bloco `bno` ocupa os bytes `[bno·B, (bno+1)·B)`. O bloco 0 é o cabeçalho; o usuário vê `bno ≥ 1`.
- Abra com `open(path, "r+b")` (ou `"w+b"` ao criar) e mantenha aberto até `close`. Use `f.seek(bno * B)` + `f.read(B)` / `f.write(data)`.
- `read` de bloco além do fim do arquivo é erro (`ValueError`), e não "bytes zerados": isso esconderia bugs.

### 2.3 Layout do cabeçalho (bloco 0)

```
offset  tam  campo
0       4    magic = b"CBD1"
4       4    block_size (u32)
8       4    high_water (u32)   # maior bno já alocado
12      4    n_free (u32)
16      4    lost_free (u32)
20      12   reservado
32      4·k  pilha de bnos livres (u32), k = n_free
```

Use `struct.Struct("<4sIIII12x")`. O cabeçalho fica **em memória** (um objeto `_FileDesc`) enquanto o arquivo está aberto. Ele é gravado em `close`/`close_all` e **não entra em `io`**: é metadado do disco simulado, e não dado do experimento.

### 2.4 Operações

```python
def allocate(self, fid) -> int:
    d = self._desc[fid]
    if d.free: return d.free.pop()
    d.high_water += 1
    # estende o arquivo físico gravando um bloco zerado? NÃO: deixa para a 1ª write.
    return d.high_water
```

- `new_page` (buffer) sempre grava o bloco antes de qualquer `read`, então não é preciso estender o arquivo no `allocate`. Mesmo assim, `read(fid, bno)` com `bno > high_water` deve dar erro.
- `free(fid, bno)`: `bno` precisa estar em `[1, high_water]` e não estar já na pilha. Se a pilha estiver cheia, incremente `lost_free`.
- `n_blocks(fid) = high_water − len(free) − lost_free`.
- `delete(fid)`: fecha e remove o arquivo do SO; o `fid` deixa de valer.
- Os `fid`s são inteiros crescentes, nunca reutilizados durante a vida do `FileManager`.

### 2.5 Contagem sequencial × aleatório

```python
def _count(self, fid, bno, write: bool):
    seq = (self._head == (fid, bno - 1))
    self._head = (fid, bno)
    for c in (self.io, self.io_by_file.setdefault(fid, IOCounters())):
        if write: 
            if seq: c.seq_writes += 1
            else:   c.rand_writes += 1
        else:
            if seq: c.seq_reads += 1
            else:   c.rand_reads += 1
```

`reset_stats()` zera `io`, `io_by_file` **e** `_head = None`. Assim a primeira leitura depois de `Engine.cold()` conta como 1 seek, como nas fórmulas (SKS: 1 seek + b transferências num scan).

---

## 3. Gerente de Buffer Pool

### 3.1 Decisões (enunciado §2.2). Coloque esta tabela no relatório

| # | Decisão | Escolha |
| :--- | :--- | :--- |
| 1 | Nº de páginas / blocos por página | soma de `BufferConfig.areas`; **1 bloco por página** |
| 2 | Repartição | **por tipo de página**: `CATALOG`, `INDEX`, `DATA`, `WORK` (área de trabalho de ordenação e junção) |
| 3 | Áreas fixas, autoajustáveis ou compartilhadas | **fixas**: medidas reproduzíveis; uma operação não rouba a área da outra |
| 4 | Políticas comparadas | **LRU** e **Clock** (plano do grupo). Implemente também **MRU** (≈15 linhas) e FIFO; a bancada escolhe 2 |
| 5 | Toss-immediate | sim, por configuração (`toss_immediate`) e dica no `unpin(toss=True)` |
| 6 | Fixação (pin) | contador `pin_count` por frame; só frame com `pin_count == 0` pode ser vítima |
| 7 | Gravação forçada | `force(page)`; usada pelo catálogo (write-through) |
| 8 | Retenção preferencial | garantida pelas áreas fixas: índice e catálogo têm frames próprios |
| 9 | Prefetch | sim, por configuração (`prefetch = k`), disparado por `pin(seq=True)` em *miss* |

> **Aviso ao grupo sobre a política:** o Clock é uma aproximação do LRU, então os números de LRU × Clock tendem a sair quase iguais. O contraste clássico dos livros é **LRU × MRU** no nested-loop: o LRU sofre *sequential flooding* e o MRU acerta. Como o MRU custa pouco, implemente as três e deixe a bancada decidir quais duas entram no relatório.

### 3.2 Estruturas

```python
class Frame:                       # é o objeto `Page` devolvido ao usuário
    __slots__ = ("idx", "area", "fid", "bno", "ptype", "data", "pin_count", "dirty", "ref")
    def mark_dirty(self): self.dirty = True

class _Area:
    ptype: PageType
    frames: list[Frame]            # tamanho fixo = cfg.areas[ptype]
    free: list[Frame]              # nunca usados ou liberados
    policy: ReplacementPolicy
    tossed: collections.deque[Frame]   # candidatos prioritários a vítima
    stats: BufferCounters

class BufferPool:
    _table: dict[tuple[int, int], Frame]   # (fid, bno) -> frame residente
    _areas: dict[PageType, _Area]
```

- `Page` (do contrato) **é** o `Frame`: exporte `Page = Frame`. Depois do `unpin`, o chamador não pode mais usar o objeto. Isso vai para o checklist de revisão.
- `data` é um `bytearray(block_size)` alocado **uma vez por frame**. Em leitura, copie para dentro com `frame.data[:] = fm.read(...)`. Nunca troque o objeto.

### 3.3 `pin`

```python
def pin(self, fid, bno, ptype, seq=False):
    area = self._areas[ptype]
    area.stats.requests += 1; self.stats.requests += 1
    f = self._table.get((fid, bno))
    if f is not None:
        # residente (deve estar na mesma área; se não, AssertionError — erro de convenção do chamador)
        area.stats.hits += 1; self.stats.hits += 1
        f.pin_count += 1
        area.policy.accessed(f)
        return f
    area.stats.misses += 1; self.stats.misses += 1
    f = self._victim(area)                       # BufferFullError se não houver
    f.data[:] = self.fm.read(fid, bno)
    self._install(f, area, fid, bno, ptype)      # _table, pin_count=1, dirty=False, policy.loaded(f)
    if seq and self.cfg.prefetch:
        self._prefetch(area, fid, bno + 1, self.cfg.prefetch)
    return f
```

`_victim(area)`:
1. `area.free`, se houver.
2. Senão, o primeiro frame de `area.tossed` com `pin_count == 0`.
3. Senão, `area.policy.victim()`, que ignora os fixados.
4. Se a vítima está suja: `fm.write`, `dirty_evictions += 1`. Remova-a de `_table` e chame `policy.removed(f)`.
5. Se nada sobrar, levante `BufferFullError(f"área {ptype}: {n} frames, todos fixados")`.

`_prefetch(area, fid, start, k)`: para `b` em `start .. start+k-1`, pare no primeiro bloco que já está residente, que passa do `high_water` ou para o qual não há vítima sem esperar. Leia com `fm.read`, instale **sem pin** (`pin_count = 0`) e conte `prefetched += 1`. A leitura sai sequencial naturalmente.

### 3.4 `unpin`, `new_page`, `force`, `flush_*`, `drop_file`, `clear`

```python
def unpin(self, page, dirty=False, toss=False):
    assert page.pin_count > 0, "unpin sem pin"
    if dirty: page.dirty = True
    page.pin_count -= 1
    area = self._areas[page.ptype]
    area.policy.unpinned(page)
    if toss and self.cfg.toss_immediate and page.pin_count == 0:
        area.tossed.append(page)

def new_page(self, fid, ptype):
    area = self._areas[ptype]
    bno = self.fm.allocate(fid)
    f = self._victim(area)
    f.data[:] = bytes(self.block_size)   # zera (sem leitura)
    self._install(f, area, fid, bno, ptype)
    f.dirty = True
    return f
```

- `force(page)`: `fm.write(page.fid, page.bno, page.data)`, `dirty = False`, `forced_writes += 1`. O frame continua fixado como estava.
- `flush_file(fid)` / `flush_all()`: grava os sujos, **sem** contar como `dirty_evictions`.
- `drop_file(fid)`: para cada frame do arquivo, faça `assert pin_count == 0`, depois descarte **sem gravar** e devolva-o a `free`.
- `free_block(fid, bno)`: se o bloco estiver residente, `assert pin_count == 0`, descarte o frame **sem gravar** (sai de `_table` e da política, volta a `free`); depois `fm.free(fid, bno)`. Sem isso, um `allocate` que reuse o `bno` encontraria um frame velho no buffer. Teste: `test_free_block_reuse`.
- `clear()`: `flush_all()`, `assert self.pinned() == 0`, todos os frames voltam a `free`, `_table` e as políticas são zeradas.
- `page(...)`: context manager com `try/finally`. Chame `unpin(p, dirty=p.dirty)` no fim; não perca a marcação de sujo.

### 3.5 `reserve_work(n)`

```python
@contextmanager
def reserve_work(self, n):
    area = self._areas[PageType.WORK]
    taken = []
    try:
        for _ in range(n):
            taken.append(self._victim(area))   # sai de free/_table/política; pode gravar sujos
        yield n
    finally:
        area.free.extend(taken)                # devolve tudo, inclusive se _victim falhou no meio
```

Os frames reservados ficam fora de `free`, de `_table` e da política, então nenhum `pin` consegue usá-los. Se `_victim` falhar no meio, o `finally` devolve o que já tinha sido tirado e o `BufferFullError` se propaga. O teste obrigatório: com `WORK = 8`, `reserve_work(8)` funciona, e um `pin(..., WORK)` dentro dele levanta `BufferFullError`.

### 3.6 Políticas (`policies.py`)

```python
class ReplacementPolicy(Protocol):
    def loaded(self, f): ...      # frame recebeu um bloco novo
    def accessed(self, f): ...    # hit
    def unpinned(self, f): ...
    def removed(self, f): ...     # frame saiu (vítima, drop, clear)
    def victim(self) -> Frame | None: ...   # só frames com pin_count == 0
    def reset(self): ...
```

| Política | Estrutura | `victim()` |
| :--- | :--- | :--- |
| LRU | `OrderedDict[frame.idx, frame]`; `loaded`/`accessed` → `move_to_end` | primeiro não fixado a partir do início |
| MRU | mesma estrutura | primeiro não fixado **a partir do fim** |
| FIFO | `OrderedDict`; só `loaded` insere no fim (hit não mexe) | primeiro não fixado do início |
| Clock | lista circular de frames da área, `ponteiro`, bit `ref` | `loaded`/`accessed` põem `ref = 1`; a varredura pula fixados, zera `ref = 1` e devolve o primeiro com `ref = 0`; no máximo 2 voltas, senão `None` |

Fábrica: `make_policy(Policy, frames) -> ReplacementPolicy`.

O LRU é medido pelo **último acesso** (pin), como em SKS 13.5. Documente essa escolha.

### 3.7 Desempenho (Python)

O caminho do `pin` com hit roda milhões de vezes. Mantenha-o curto: uma busca no dict, dois incrementos e `policy.accessed`. Nada de `logging` nem de criar objetos no caminho quente. Use `__slots__` no `Frame`.

---

## 4. Testes obrigatórios

| Teste | Verifica |
| :--- | :--- |
| `test_roundtrip` | gravar, fechar, reabrir e ler devolve os mesmos bytes; o cabeçalho persiste `high_water` e os livres |
| `test_free_reuse` | `free(5)`, e o próximo `allocate` devolve 5; `n_blocks` correto |
| `test_seq_vs_rand` | ler 1..10 em ordem, com cache frio, dá `seq_reads == 9` e `rand_reads == 1` |
| `test_hit_miss` | pin no mesmo bloco 2× dá 1 miss e 1 hit; `hit_rate == 0.5` |
| `test_dirty_eviction` | área com 2 frames: suja A, carrega B e C; A é gravado (`dirty_evictions == 1`); ao reler, o conteúdo novo está lá |
| `test_not_dirty_no_write` | página alterada **sem** `mark_dirty` é perdida. Prova que o contador não inventa gravação e serve de exemplo do bug do enunciado |
| `test_buffer_full` | todos os frames fixados → `BufferFullError` |
| `test_lru_mru_clock_fifo` | sequência de referência clássica com 3 frames: compare a sequência de vítimas com a calculada à mão (coloque o cálculo no comentário) |
| `test_sequential_flooding` | área de 4 frames; varrer 5 blocos 3×. LRU: 15 misses. MRU: bem menos. É o resultado que vai para o relatório |
| `test_toss` | com `toss_immediate=True`, a página desafixada com `toss` é a próxima vítima |
| `test_prefetch` | `prefetch=4`: um pin `seq` em bloco frio lê 5 blocos; os 4 pins seguintes são hits |
| `test_reserve_work` | §3.5 |
| `test_areas_isolated` | encher `DATA` não expulsa páginas de `INDEX` |
| `test_force` | `force` grava já e incrementa `forced_writes` |
| `test_drop_file` | frames descartados sem gravação; `io.writes` não muda |

---

## 5. Armadilhas que a IA costuma gerar aqui (enunciado §7.1). Procure ativamente

- `pin` que devolve uma **cópia** de `data`: a alteração some e ninguém percebe.
- `unpin` que zera `dirty` ou ignora o parâmetro.
- Política que escolhe frame **fixado** como vítima.
- Clock que entra em laço infinito quando todos estão fixados.
- `clear()` que não grava os sujos antes de esvaziar.
- Contagem de leitura em *hit* (ou de request em `new_page`).
- `read` além do fim do arquivo devolvendo zeros em silêncio.
- `reset_stats` que não zera a posição da cabeça, deixando o primeiro acesso como "sequencial".
- Prefetch que **fixa** as páginas lidas antecipadamente: vazamento de pin.

Registre cada uma que aparecer em `ai/P1/review.md`.

---

## 6. Testes de estresse (depois de 12/11, coordenação)

Você monta `tests/stress/` e o relatório `ai/STRESS.md`. Cada dono escreve o estresse do seu módulo; você integra, roda e consolida. Marque com `@pytest.mark.stress` e rode com `pytest -m stress`.

| Alvo | Estresse | Oráculo |
| :--- | :--- | :--- |
| Buffer (seu) | 1 milhão de operações aleatórias de pin/unpin/dirty em área minúscula (3 frames) e 10 mil blocos | modelo de referência simples (dict Python que simula a mesma política) e conteúdo correto após `clear` |
| Heap/Hash (P2) | 100 mil inserts/deletes aleatórios | `dict` Python; `bp.pinned() == 0` |
| B+ (P3) | 100 mil inserts/deletes aleatórios, chaves repetidas; checar invariantes a cada 1000 operações | lista Python ordenada com `bisect` |
| Joins/sort (P4/P5) | `M = 3` (mínimo), tabelas 50× maiores que M, chaves todas iguais (skew total) | resultado do SQLite (P6) |
| Sistema | base completa, todas as configurações, 1 repetição | nenhuma exceção, `pinned() == 0`, resultados = SQLite |

**Ingênuo × refatorado** (exigência do §7.2.3.d): cada dono guarda em `ai/Px/raw/` uma versão **executável** do código bruto da IA para pelo menos um componente crítico. Você roda o mesmo estresse nas duas versões e tabela as falhas, o tempo e o I/O. Exemplos: B+ ingênuo sem merge (ocupação cai), join que carrega tudo em lista (estoura a área), buffer que copia páginas (perde gravações).

---

## 7. `POST_MORTEM.md` (raiz). Você consolida

Estrutura exigida pelo enunciado §7.2.3.d:

1. **Code review crítico:** os prompts-chave e os fragmentos brutos da IA, antes de qualquer edição. Tire de `ai/Px/prompts.md` e `ai/Px/raw/`, no máximo 3 por pessoa, escolhendo os mais instrutivos.
2. **Justificativa de refatoração:** o que mudou para atender desempenho, correção e estresse. Tire de `ai/Px/review.md`.
3. **Evidência de testes:** a tabela ingênuo × refatorado do §6.
4. **Lições:** que tipos de erro a IA cometeu mais e como foram achados. As contagens que não bateram com as fórmulas de custo são as melhores evidências.

Cobre os colegas a partir do dia 14/11 para manter `ai/Px/` em dia. Reconstruir isso no fim não funciona.

---

## 8. Ordem de trabalho sugerida

1. Dia 1: esqueleto, `contracts.py`, `conftest.py`. Faça o push logo para todos poderem começar.
2. `FileManager` completo com testes (é pequeno).
3. `BufferPool` com uma área e LRU, `pin`/`unpin`/`new_page`/`page()`/`flush_all`/`clear`. **PR no `main` até 05/11.**
4. Áreas, demais políticas, toss, prefetch, `force`, `drop_file`, `reserve_work`, `stats_by_area`. Até 12/11.
5. Teste de *sequential flooding* com números para o relatório.
6. Estresse e post-mortem.
