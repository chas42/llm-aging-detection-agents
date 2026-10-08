# 2. Arquitetura

## Duas camadas

O projeto separa **o que exige julgamento** (LLM) do **que precisa ser reproduzível** (código
determinístico):

| camada | componentes | característica |
|---|---|---|
| **Agentes (LLM)** | instrumentação, geração de workload, julgamento de ativação, reparo, revisão, validação funcional | leem a finding card e o código, produzem artefatos (código, scripts, veredito) |
| **Harness (determinístico)** | `run_diagnostic.py`, `monitor.py`, `analyze_run.py`, `functional_check.py`, `deploy/*.sh` | executam, medem e calculam a estatística; nunca usam LLM |

Por isso, **nenhum agente calcula estatística** (ele interpreta a saída do `analyze_run.py`) e
**nenhum LLM roda na máquina isolada** (ela só recebe os artefatos prontos).

## Fluxo de dados

```
findings/<id>.yaml ─────────────────────────────────────────────┐ (lido por todos os agentes)
        │                                                      │
apps/<app>/original ──[instrumentation agent]──► apps/<app>/instrumented
                                                       │
reference/ (API, .jmx) ──[workload generator]──► workloads/<id>_targeted_vN.py, _control.py
                                                       │
                 [run_diagnostic.py] ◄─────────────────┘   (máquina isolada, ver deploy/)
                  ou server_agent.py (servidor) + run_remote.py (cliente) em dois PCs
                        │
                        ▼
              runs/<ts>_<label>/ {meta.json, monitor.csv, client.csv, instrumentation.jsonl, workdir/}
                        │
              [analyze_run.py] ──► runs/<...>/analysis/{summary.json, summary.md, series.png}
                        │
              [activation judge] ──► runs/<...>/verdict.md
                        │ ACTIVATED
apps/<app>/instrumented ──[repair agent ⇄ reviewer]──► apps/<app>/repaired
                        │
              [functional validator] → [run_diagnostic.py, mesmo workload/seed] → [analyze --compare] → [judge]
```

## Contratos

Os componentes conversam por formatos fixos. É isso que permite trocar um agente (ou levar o
fluxo para o VoltAgent) sem quebrar os outros.

### Finding card (`findings/*.yaml`)
É a fonte única de verdade sobre o achado. Campos principais:
- `mechanism`: `resource`, `trigger`, `gap`, `suspect_code` (funções);
- `controllability.level`: `workload` | `environment` | `uncontrollable`;
- `baseline_workload`: por que o experimento original não ativou;
- `activation_criteria`: `path_executed`, `resource_growth`, `control_contrast`;
- `expected_symptoms`: séries que contam como sintoma;
- `loop.max_workload_revisions`: limite do loop.

### Instrumentação
- Probes num módulo separado, `aging_probe.py`. No código da app entram só chamadas mínimas,
  cada linha marcada com `# AGING-PROBE`.
- Desligada por padrão; liga com `AGING_INSTRUMENT=1`.
- Saída JSONL em `$AGING_INSTRUMENT_LOG`, com 3 tipos de linha:
  - `{"ts":…, "kind":"start", "probes":[…]}`
  - `{"ts":…, "kind":"snapshot", "metrics":{"rows.total": 1234, …}}`, a cada
    `AGING_SNAPSHOT_INTERVAL` s (padrão 10)
  - `{"ts":…, "kind":"event", "probe":"heartbeat_error", "fields":{…}}`, com limite de taxa
- As probes não podem acumular estado (nada de listas ou dicts indexados por dados de
  requisição), senão elas mesmas virariam um mecanismo de envelhecimento.

### Workload
```
python <script> --base-url URL --duration S --seed N --out client.csv [parâmetros próprios]
```
- `client.csv`: `ts,endpoint,status,latency_ms,resp_bytes[,phase]`, uma linha por requisição,
  gravada em streaming.
- Determinístico pela seed; closed-loop com ritmo controlado (se o servidor degrada, a vazão
  cai, e não se forma fila no cliente).
- `endpoint` é o nome da série na análise. Por isso, leituras de tokens diferentes usam nomes
  diferentes (`services_grow`, `services_ref4`…).

### Pasta de execução (`runs/<YYYYmmdd-HHMMSS>_<label>/`)
`meta.json`, `monitor.csv`, `client.csv`, `instrumentation.jsonl`, `server.log`, `workload.log`,
`workdir/` (cópia da app + DB final), `analysis/`, `verdict.md`. Detalhes em [04](04-harness-e-estatistica.md).

## Princípios de projeto

1. **Nunca modificar o original.** Cada agente escreve numa pasta irmã nova (`instrumented/`,
   `repaired/`). `apps/*/original/` e `reference/` são só de leitura. Os projetos
   `baxbench_LLM_code` e `generated-software-llm` também não são tocados.
2. **Um achado entra, um patch sai.** O reparo fecha só o mecanismo da finding card, para que a
   diferença entre antes e depois seja atribuível a uma única mudança.
3. **Medição idêntica antes e depois.** O reparo é aplicado **sobre a cópia instrumentada** e não
   pode tocar nas linhas `# AGING-PROBE`. Assim, R1 (antes) e R2 (depois) usam as mesmas probes, o
   mesmo workload, a mesma seed e a mesma duração.
4. **Controles explícitos.** Toda afirmação de ativação é contrastada com uma execução onde o
   gatilho está ausente.
5. **Cada execução descreve a si mesma.** `meta.json` guarda parâmetros e hashes do código; a pasta
   guarda a cópia exata da app usada e o banco final.
6. **Pontos de checagem humana** antes de execuções longas e antes do reparo.
7. **Revisão adversarial.** O patch é avaliado por outro agente, sem memória de como foi escrito.

## Mapeamento para o diagrama do e-mail

| caixa do diagrama | neste projeto |
|---|---|
| AI-based static aging analysis / findings | `reference/static-analysis/*.md` (trabalho anterior) |
| Finding classification | `findings/*.yaml` (manual no piloto) |
| Targeted workload and environment generation | `aging-workload-generator` (+ `aging-instrumentation-agent`, sugestão do Roberto) |
| Diagnostic execution | `harness/run_diagnostic.py` na máquina isolada (`deploy/`) |
| Aging validation / "Was the finding activated?" | `harness/analyze_run.py` + `aging-activation-judge` |
| Revise workload or move to another finding | loop judge → workload generator (≤ 3) |
| LLM-based repair agent | `aging-repair-agent` ⇄ `aging-repair-reviewer` |
| Functional validation | `aging-functional-validator` / `harness/functional_check.py` |
| Re-execution / before-after comparison | `run_diagnostic.py` + `analyze_run.py --compare` + judge |
