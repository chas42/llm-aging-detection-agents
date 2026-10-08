# Documentação: aging-validation

Pipeline que **valida em tempo de execução** os achados de envelhecimento de software
apontados pela análise estática e depois **repara** os que forem confirmados. Este é o
piloto com o Uptime FastAPI.

## Ordem de leitura sugerida

| # | documento | para entender |
|---|---|---|
| 1 | [01-visao-geral.md](01-visao-geral.md) | contexto da pesquisa, problema, objetivo, workflow e glossário |
| 2 | [02-arquitetura.md](02-arquitetura.md) | componentes, contratos entre eles, fluxo de dados e princípios de projeto |
| 3 | [03-agentes.md](03-agentes.md) | os 6 agentes LLM: papel, entradas, saídas, regras e como invocar |
| 4 | [04-harness-e-estatistica.md](04-harness-e-estatistica.md) | scripts determinísticos, métricas coletadas e como ler a estatística |
| 5 | [05-piloto-uptime-F4.md](05-piloto-uptime-F4.md) | o experimento atual: app, achado, instrumentação, workloads e critérios |
| 6 | [../deploy/README.md](../deploy/README.md) | **tutorial** para rodar os experimentos numa máquina isolada |
| — | [PLANO.md](PLANO.md) | plano de fases, status atual e decisões tomadas |

## Mapa rápido do repositório

```
aging-validation/
├── findings/        finding cards (YAML): o "contrato" de cada achado
├── apps/            original/ · instrumented/ · repaired/ · repaired-reference/
├── workloads/       geradores de carga (direcionado, controle) + justificativa (.md)
├── harness/         execução, monitoramento, análise e teste funcional (sem LLM)
├── deploy/          pacote e scripts para a máquina isolada + tutorial
├── runs/            evidências de cada execução (uma pasta por execução)
├── reference/       cópias só de leitura: relatório estático, teste BaxBench, .jmx original
├── .claude/agents/  os 6 subagentes do pipeline
└── docs/            esta documentação
```

## Comandos mais usados

```bash
.venv/bin/python harness/functional_check.py --app apps/uptime-fastapi/instrumented --test reference/tests/test_uptime.py --instrument
.venv/bin/python harness/run_diagnostic.py --app apps/uptime-fastapi/instrumented --instrument \
    --workload workloads/uptime-fastapi-F4_targeted_v1.py --duration 600 --label teste_local
.venv/bin/python harness/analyze_run.py runs/<execução>
.venv/bin/python harness/analyze_run.py runs/<antes> --compare runs/<depois>
bash deploy/pack.sh                       # gera o bundle para a máquina isolada
```
