# 5. Piloto: Uptime FastAPI, achado F4 (+F5)

## A aplicação

`apps/uptime-fastapi/original/app.py` vem de `baxbench_LLM_code/coding-tasks/python-fastAPI/UptimeService`
(gerado com GPT-5.2 Thinking), extraído do **git HEAD**, ou seja, sem o patch que estava
pendente naquele repositório.

- `POST /heartbeat {serviceId, token}`: registra que o serviço está vivo (upsert no SQLite).
- `POST /services {token}`: lista os serviços daquele token e o último heartbeat de cada um.
- SQLite em `db.sqlite3`, tabela `heartbeats(service_id, token, last_notification)`, PK
  `(service_id, token)` e índice em `token`.
- Um `threading.Lock` global (`_db_lock`) serializa **todas** as operações de banco, e cada uma
  abre e fecha uma conexão.
- Os handlers são `def` síncronos, então rodam no pool de threads do FastAPI.

## O achado

Da [análise estática](../reference/static-analysis/uptime_fastAPI_report.md):

- **Finding 4: crescimento sem limite das linhas por token** (*static aging mechanism
  supported*). Cada serviceId novo insere uma linha que nunca é apagada: não há limite, TTL nem
  caminho de remoção.
- **Finding 5: `fetchall()` sem paginação** (*conditionally plausible*, combinado com o F4).
  `/services` lê todas as linhas do token dentro do lock global e monta um objeto pydantic por
  linha.

A cadeia completa: **gatilho** (serviceIds distintos) → **acúmulo** (linhas no SQLite) →
**sintoma** (custo crescente de `/services` e, pelo lock, de todas as requisições).

**Por que nunca foi observado:** o `.jmx` do paper tem 1 thread e 4 serviços fixos
(`svc1..svc4`), então a tabela fica com 4 linhas para sempre.

Os outros achados do mesmo relatório (lock global, conexão por requisição, falta de pragmas e de
timeout no lock) foram classificados como *non-aging* e não fazem parte deste piloto.

## Instrumentação (`apps/uptime-fastapi/instrumented/`)

Gerada pelo `aging-instrumentation-agent`. São cerca de 20 ganchos `# AGING-PROBE` no `app.py`, e
a lógica fica em `aging_probe.py`. Métricas do snapshot (a cada 10 s):

| métrica | mede | evidencia |
|---|---|---|
| `calls.upsert_heartbeat`, `calls.list_services_for_token`, `calls.services` (+ `window.calls.*`) | quantas vezes cada caminho suspeito rodou | caminho executado |
| `rows.total`, `rows.max_per_token`, `tokens.distinct` | `COUNT(*)` da tabela, linhas do maior token, número de tokens | **crescimento do recurso** |
| `db.bytes`, `db.main_bytes` | tamanho do DB (com e sem journal/WAL) | sintoma (disco) |
| `fetch_rows.max`, `services.serialized_rows.max`, `rows_fetched.total` | linhas lidas e serializadas por `/services` | recurso visto pelo caminho de leitura (F5) |
| `fetch_ms.max`, `upsert_ms.max`, `services.handler_ms.max` | tempo segurando o lock na leitura e na escrita; tempo do handler | sintoma (latência) |
| `lock_wait_ms.max` (`.upsert`, `.list`) | espera pelo lock global | sintoma (contenção, vazão) |
| `errors.*` + eventos `*_error` | ramos de erro 500 | caminhos raros |
| `snapshot.query_ms`, `snapshot.lock_wait_ms`, `snapshot.lock_timeout` | custo da própria instrumentação | controle de overhead |

**Limitação:** a validação e a serialização JSON da resposta acontecem depois que o handler
retorna, então ficam fora do `services.handler_ms`. A latência e o tamanho medidos no cliente são
a referência para esse sintoma.

## Workloads (`workloads/`)

### Direcionado: `uptime-fastapi-F4_targeted_v1.py`
Gerado pelo `aging-workload-generator`. A justificativa completa está no `.md` ao lado. Fluxos
com ritmo controlado:

| fluxo (`endpoint`/`phase`) | o que faz | papel |
|---|---|---|
| `heartbeat`/`grow` | serviceId **sempre novo** para `tenant-grow-1`, 10/s | **gatilho** |
| `heartbeat`/`refresh` | reenvia um serviceId já existente, 2/s | caminho de update (realismo) |
| `heartbeat`/`prime`, `ref` | registra e mantém os tokens de referência (4 e 200 serviços), 2/s | tamanho constante |
| `services_grow`/`probe` | `/services` do token que cresce, a cada 2 s | **sintoma** |
| `services_ref4`, `services_ref200`/`refread` | `/services` dos tokens de tamanho fixo, a cada 2 s | **controle interno** |

Carga: cerca de 14 escritas/s. Cada escrita custa ~17 ms (dominada por `fsync`) e o teto é de
~55–60/s, então o lock fica ~25% ocupado no início. Estimativa para 1 h: ~36 mil linhas, resposta
de `/services` de ~3 MB, DB de ~4 MB.

Opção `--key-space N` (adicionada para o controle pareado): o fluxo `grow` passa a circular entre
N serviceIds fixos, na **mesma taxa**, e vira a fase `cycle`.

### Controle: `uptime-fastapi-F4_control.py`
Réplica fiel do `.jmx`: 1 thread, sem pausa, heartbeats `svc1/pass1..svc4/pass4` seguidos de
`/services` para `pass1..pass4`.

## Execuções planejadas (campanha)

| execução | app | workload | expectativa |
|---|---|---|---|
| **R0** `R0_F4_control_jmx` | instrumentada | controle (`.jmx`) | `rows.total` = 4 o tempo todo → **não ativado** |
| **R0b** `R0b_F4_matched_control` | instrumentada | direcionado `--key-space 4` | mesma carga da R1, `rows.total` constante (208) → **não ativado**; isola a variável "chaves distintas" |
| **R1** `R1_F4_targeted_v1` | instrumentada | direcionado | `rows.total` cresce ~10/s → **ativado**; procurar sintomas |
| R2 (Fase 6) | reparada | direcionado, mesma seed e duração | recurso limitado, sem sintoma |

Por que dois controles? A R0 roda em outro regime de carga: sem pausa, perto do teto de escrita.
Ela mostra que o workload *do paper* não ativa o achado, mas não isola a causa. A R0b tem a mesma
carga da R1 e muda **só** a diversidade de chaves.

## Critérios (da finding card)

- **Ativado:**
  - as probes mostram os caminhos executando;
  - `rows.total`/`rows.max_per_token` com tendência MK crescente (p < 0,05) e razão final/inicial ≥ 10;
  - nos controles, as linhas ficam constantes.
- **Sintomas esperados**, em ordem de probabilidade:
  1. latência p50/p95 e tamanho da resposta de `services_grow`;
  2. tamanho do DB;
  3. queda da vazão e aumento da espera no lock (o `/services` grande segura o lock e atrasa os
     heartbeats);
  4. latência de `/heartbeat` (B-tree maior).
- **Provavelmente sem sintoma:** RSS. O estado fica em disco, e na memória só aparecem picos por
  chamada. Se o RSS não crescer, isso também é um resultado relevante para o paper: ativado, com
  envelhecimento em latência e não em memória.
- **Controle interno:** `services_ref4` e `services_ref200` devem ficar estáveis. Se também
  piorarem, o efeito é global (contenção do lock, índice maior) e não só do volume lido.

## Expectativa para o reparo (Fase 6)

Um limite com eviction ou um TTL por token deve deixar linhas, latência e tamanho de `/services`
sem tendência, com um custo constante pequeno no `/heartbeat`. O patch antigo
(`repaired-reference/`, limite de 1000 por token com `DELETE … NOT IN (… ORDER BY … LIMIT)` a cada
escrita) serve de comparação com o patch que o agente vai gerar agora.

## Resultados até agora

| data | o quê | resultado |
|---|---|---|
| 04/10 | teste funcional: original, instrumentada (probes off/on) | PASS |
| 04/10 | smoke 60 s: controle | 6.284 requisições, 0 erros, tabela = 4 linhas |
| 04/10 | smoke 60 s: direcionado | 0 → 564 linhas no token que cresce; resposta 5,5 kB → 51 kB; 0 erros |
| 04/10 | smoke 30 s: controle pareado | 574 heartbeats, 0 erros, linhas constantes |
| 04/10 | teste de ponta a ponta do bundle (pack → setup → preflight → campanha de 40 s → coleta) | OK |
