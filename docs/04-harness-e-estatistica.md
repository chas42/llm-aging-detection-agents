# 4. Harness e estatística

O harness é o código **determinístico** do projeto. Ele não usa LLM, e é ele que vai para a
máquina isolada.

## Scripts

### `harness/run_diagnostic.py`: uma execução diagnóstica
1. copia a app para `runs/<ts>_<label>/workdir/` **sem** bancos de dados, de modo que toda
   execução começa com o DB vazio;
2. sobe o servidor com `python -m uvicorn app:app --host 127.0.0.1 --port 8000`, opcionalmente
   preso a CPUs com `taskset` (`--app-cpus`);
3. espera a porta abrir (até 30 s) e liga o monitor;
4. roda o workload (`--load-cpus`), passando `--base-url --duration --seed --out` e o que vier
   depois de `--`;
5. derruba o servidor (SIGINT → SIGTERM → SIGKILL) e grava o `meta.json`.

Parâmetros importantes: `--instrument` (liga as probes), `--interval` (amostragem do monitor,
padrão 5 s) e `--watch` (arquivos cujo tamanho é medido, padrão `db.sqlite3`). Se a porta já
estiver em uso, ele se recusa a rodar.

### `harness/monitor.py`: lado do servidor
É o sucessor dos scripts `collect-data` do paper (que usavam `ps`), agora com `psutil`. Amostra o
processo do servidor e seus filhos:

| coluna | significado |
|---|---|
| `rss_bytes` | memória residente (o que o paper chamava de RSS) |
| `uss_bytes` | memória exclusiva do processo (melhor para detectar vazamento) |
| `vms_bytes` | memória virtual (VSZ) |
| `cpu_percent`, `num_threads`, `num_fds` | CPU, threads, descritores abertos (vazamento de socket/arquivo) |
| `watched_bytes` | tamanho do DB + `-journal`/`-wal`/`-shm` |
| `sys_mem_used_bytes` | memória usada no sistema todo (contexto) |

### `harness/analyze_run.py`: análise de tendências
Lê `monitor.csv`, `client.csv` e `instrumentation.jsonl` e monta as séries temporais:

| prefixo | origem | exemplos |
|---|---|---|
| `server.*` | monitor (amostra bruta) | `server.rss_mb`, `server.num_fds`, `server.watched_files_mb` |
| `client.<endpoint>.*` | cliente, agregado em janelas (`--window`, padrão 60 s) | `latency_p50_ms`, `latency_p95_ms`, `resp_bytes_mean`, `throughput_rps`, `error_rate` |
| `instr.*` | snapshots das probes | `instr.rows.total`, `instr.fetch_ms.max`, `instr.lock_wait_ms.max` |

Descarta os primeiros `--warmup` segundos (padrão 60) e as janelas parciais nas bordas. Gera
`analysis/summary.json`, `summary.md` e `series.png`. Com `--compare <depois>`, gera também a
tabela e os gráficos antes × depois.

### `harness/functional_check.py`: teste funcional
Roda as funções `func_test*` do teste BaxBench (`reference/tests/test_uptime.py`) contra uma
cópia temporária da app na porta 3000 (o teste usa `localhost:3000` fixo). Código de saída: 0 =
PASS, 1 = FAIL, 2 = SETUP_FAILED.

### `harness/smoke_workload.py`
Workload trivial usado só para testar o próprio harness. Não é experimento.

---

## Estatística

### Mann-Kendall
Teste não paramétrico de **tendência monotônica**. A hipótese nula é que não há tendência. É o
mesmo teste usado no paper. O harness reporta duas versões:
- `mk_p`: teste original;
- `mk_hr_p`: **modificação de Hamed-Rao**, que corrige a autocorrelação. Séries de latência e
  memória são fortemente autocorrelacionadas, e o teste original tende a dar p-valores
  pequenos demais. **Prefira o `mk_hr_p`.** Os scripts R do paper também usavam um
  Mann-Kendall modificado.

### Sen's slope
Mediana das inclinações entre todos os pares de pontos. É robusto a outliers. O harness calcula
a inclinação **por hora**, em tempo real (não por índice de amostra). Exemplo: `server.rss_mb`
com slope 2,5 significa +2,5 MB/h.

### Tamanho do efeito (`sen_change_pct`)
É a variação implícita pela inclinação ao longo da execução inteira, relativa à mediana:
`slope × duração_h / |mediana| × 100`.

**Por que existe:** no teste do harness (90 s), o Mann-Kendall acusou "tendência crescente"
(p = 0,019) numa latência que variou 1,2%. Com muitas amostras, até variações desprezíveis
ficam significativas. Significância estatística não é o mesmo que envelhecimento.

### Critério de sintoma
Usado pelo `aging-activation-judge`. Uma série conta como sintoma de envelhecimento só se:

| | condição |
|---|---|
| (a) | Mann-Kendall p < 0,05 (de preferência Hamed-Rao) |
| (b) | sinal degradante: latência, tamanho, memória ou erros **sobem**, ou vazão **cai** |
| (c) | efeito ≥ 10% ao longo da execução (`sen_change_pct`) |
| (d) | a mesma tendência **não** aparece no controle |

### Como ler o `summary.md`

```
| series                          | n  | first | last | Sen slope /h | Sen change % | MK p   | MK-HR p | trend      |
| client.services_grow.latency_p50_ms | 58 | 3.1 | 95.4 | 98.7        | 540         | 1e-20  | 3e-08   | increasing |
```
Leitura: a latência mediana do `/services` do token que cresce subiu de 3,1 para 95,4 ms; a
inclinação é de +98,7 ms/h, o que equivale a +540% sobre a mediana; a tendência é significativa
mesmo com a correção de autocorrelação. *(Valores ilustrativos.)*

`trend` vale `increasing`, `decreasing`, `no trend` ou `constant` (série sem variação).

---

## Cuidados de medição

- **Mesma máquina para app e carga:** isolados por CPU (`taskset`). O juiz verifica se o cliente
  saturou (timeouts, CPU).
- **WSL e laptops:** as medições servem para depuração. Para resultados do paper, use a máquina
  isolada ([deploy/README.md](../deploy/README.md)).
- **Overhead das probes:** a thread de snapshot segura o lock da app a cada 10 s para contar
  linhas. O próprio custo vai para `instr.snapshot.query_ms` e `instr.snapshot.lock_wait_ms`, e o
  juiz compara esse custo com o efeito observado.
- **Comparações antes × depois:** mesma máquina, mesmo workload, mesma seed, mesma duração e
  mesmo `APP_CPUS`/`LOAD_CPUS`. O `meta.json` permite conferir.
