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
| 3 | Campanha na **máquina isolada**: R0 controle `.jmx`, R0b controle pareado, R1 direcionado (1 h cada) | `deploy/campaign_uptime_F4.sh` | `results/*.tar.gz` → `runs/` | ✅ 12/10, 2 PCs, 1 h cada, sem erros |
| 4 | Julgamento de ativação (loop ≤ 3 revisões do workload) | `aging-activation-judge` | `runs/*/verdict.md` | ✅ **ACTIVATED_WITH_AGING** na 1ª iteração |
| — | **Checkpoint 2**: resultado de ativação | Cesar | | |
| 5 | Reparo → revisão → validação funcional | agentes de reparo (portados) | `apps/uptime-fastapi/repaired/` | ✅ APPROVE + PASS |
| 6 | R2: app reparado + mesmo workload/seed/duração da R1; comparação | harness + juiz | `comparison_vs_*.md/png` | ⏳ aguardando `RUNS="R2"` nos 2 PCs |
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
- 12/10: **campanha (Fase 3)** em 2 PCs (servidor `cesar-pc`, kernel 6.2 = Ubuntu 22.04 HWE; LAN com
  RTT ~1,5 ms; relógios corrigidos por execução, offset 13–29 ms). R0, R0b e R1 com 1 h cada, 0 erros
  em todas. Aviso: o governor da CPU do cliente estava em `powersave`.
- 13/10: **juiz (Fase 4): ACTIVATED_WITH_AGING** na 1ª versão do workload
  (`runs/20261012-224353_R1_F4_targeted_v1/verdict.md`):
  - linhas 875 → 36.020 (9,95 chaves novas/s, alvo 10); controles constantes (4 e 208);
  - `/services` p50 28,7 → 473 ms (+193%), p95 46 → 489 ms, resposta 79 KB → 3,2 MB;
  - heartbeat p95 35,7 → 205 ms (+142%), por espera no lock (`/services` grande segura o lock global);
  - DB 0,16 → 5,4 MB;
  - **RSS 55 → 107 MB (+62%)**, contrariando a expectativa do card (estado em disco). Cresce em serra e
    está estabilizando; correlação 0,94 com o tamanho da maior resposta. Hipótese, ainda não medida:
    o alocador do Python retém o pico de memória de cada `/services`. É um sintoma secundário;
  - descartados como explicação: saturação do cliente (CPU ≤ 15%, taxas no alvo), overhead da
    instrumentação (<1% do efeito), rede e aquecimento. Os tokens de referência variaram <5% (abaixo do limiar).
- 13/10: **reparo (Fase 5)**. O `aging-repair-agent` gerou `apps/uptime-fastapi/repaired/app.py`:
  limite por token (`MAX_SERVICES_PER_TOKEN`, padrão 1000) com remoção dos serviços há mais tempo sem
  heartbeat, num índice novo `(token, last_notification)` sem ordenação, mais `LIMIT` na leitura de
  `/services`. Rejeitou TTL porque apagaria justamente os serviços caídos que o monitor deve mostrar.
  O `aging-repair-reviewer` deu **APPROVE**, com experimentos em SQLite: comportamento idêntico até o
  limite, probes intactas, remoção ~46 µs por chave nova. Riscos não bloqueantes: banco antigo acima
  do limite e relógio voltando no tempo. Validação funcional: **PASS** com probes desligadas e ligadas.
  No teste local com limite 200, as linhas param em 200 e a resposta estabiliza em 18 KB.
- 13/10: a campanha ganhou a execução **R2** (`RUNS="R2"`): o workload, seed e duração da R1 sobre o
  app reparado. O bundle e o preflight do servidor passaram a incluir o app reparado.
