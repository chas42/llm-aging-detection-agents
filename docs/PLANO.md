# Plano do piloto: validação de envelhecimento guiada por análise estática

Contexto: e-mails de Ermeson/Roberto (21–25/08/2026). Piloto manual / com scripts leves sobre o
**Uptime FastAPI**, com o achado de crescimento ilimitado de serviços por token (F4+F5). A
implementação em VoltAgent fica para uma etapa posterior.

Decisões tomadas (04/10/2026):
- App alvo: `baxbench_LLM_code/coding-tasks/python-fastAPI/UptimeService` (versão original do git HEAD).
- Workload: script Python (asyncio + httpx), não JMeter.
- Duração de cada execução diagnóstica: 1–2 h.
- Etapas com LLM implementadas como subagentes do Claude Code (`.claude/agents/`).
- Projeto separado. Os projetos existentes são apenas fontes de leitura.

## Fases

| # | Fase | Responsável | Saída | Status |
|---|---|---|---|---|
| 0 | Preparação: projeto, venv, app original, finding card, harness | manual | `findings/`, `harness/`, `apps/*/original` | ✅ feito |
| 1 | Instrumentação | `aging-instrumentation-agent` + `functional_check` | `apps/uptime-fastapi/instrumented/` | ✅ feito (PASS com probes on/off) |
| 2 | Workload direcionado + controle | `aging-workload-generator` | `workloads/` | ✅ feito (smoke 60 s: 0→564 linhas; controle fixo em 4) |
| — | **Checkpoint 1** concluído: execuções vão para máquina isolada | Cesar | `deploy/` | ✅ |
| 3 | Campanha na **máquina isolada**: R0 controle `.jmx`, R0b controle pareado, R1 direcionado (1 h cada) | `deploy/campaign_uptime_F4.sh` | `results/*.tar.gz` → `runs/` | ⏳ aguardando execução ([tutorial](../deploy/README.md)) |
| 4 | Julgamento de ativação (loop ≤ 3 revisões do workload) | `aging-activation-judge` | `runs/*/verdict.md` | |
| — | **Checkpoint 2**: resultado de ativação | Cesar | | |
| 5 | Reparo → revisão → validação funcional | agentes de reparo (portados) | `apps/uptime-fastapi/repaired/` | |
| 6 | R2: app reparado + mesmo workload/seed/duração da R1; comparação | harness + juiz | `comparison_vs_*.md/png` | |
| 7 | Relatório do piloto para Ermeson/Roberto | manual | `docs/` | |

## Critérios (resumo; a fonte é `findings/uptime-fastapi-F4.yaml`)

- **Ativado**: caminhos suspeitos executados + contagem de linhas com tendência MK crescente (p < 0,05) e crescimento ≥ 10× + controle sem crescimento.
- **Sintoma de envelhecimento**: série com MK p < 0,05 (Hamed-Rao), sinal degradante, efeito ≥ 10% na execução e ausente no controle.
- Sintomas esperados: latência e tamanho da resposta de `/services`, tamanho do DB, latência de `/heartbeat`. O RSS provavelmente **não** cresce, porque o estado fica em disco.

## Observações já registradas

- O workload original (`reference/uptime_test.jmx`) tem 1 thread e 4 serviceIds fixos, então o achado nunca foi ativado no experimento de 48 h.
- O `app.py` em `baxbench_LLM_code` tem um patch não commitado (cap de 1000 por token com eviction). Ele foi copiado para `apps/uptime-fastapi/repaired-reference/` apenas como referência.
- Smoke test do harness (90 s, workload fixo): com n pequeno, o MK acusa tendências desprezíveis (p50 do heartbeat +1,2%). Por isso, o critério exige tamanho de efeito (`sen_change_pct`).
- Ambiente: WSL2, 6 CPUs, Python 3.12. App e carga na mesma máquina, isolados por `taskset` (app na CPU 0, carga nas CPUs 1–3). Latência de base: heartbeat ~17 ms (dominada por fsync), services ~1,7 ms.
- JMeter e R não estão instalados. A estatística é feita em Python (`pymannkendall`), e a verificação cruzada com os scripts R fica opcional.
- 04/10: decidido rodar os experimentos numa **máquina isolada**. Foi criado o pacote `deploy/`
  (pack → setup → preflight → campanha → coleta), testado de ponta a ponta numa pasta temporária.
- 04/10: adicionado o **controle pareado R0b** (`--key-space 4` no workload direcionado: mesma carga,
  sem chaves novas), porque o controle fiel ao `.jmx` roda num regime de carga diferente (~90% do
  teto de escrita, contra ~25%).
- 04/10: documentação completa em `docs/` (comece por `docs/README.md`).
- 08/10: adicionado o **modo com dois PCs** (servidor = app + coleta; cliente = carga), como no paper
  original. Um agente de controle HTTP no servidor (`harness/server_agent.py`, com token) é dirigido pelo
  cliente (`harness/run_remote.py`), que também corrige a diferença de relógio. Testado de ponta a ponta
  com dois bundles (preflight server/client e campanha de 45 s por execução). Ver `deploy/README.md`.
