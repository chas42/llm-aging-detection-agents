# Plano: etapa de identificação (código → finding cards)

Fecha o ciclo do workflow. Hoje o pipeline começa numa finding card escrita à mão a partir do
relatório do paper anterior. Esta etapa gera as finding cards automaticamente, combinando
**ferramentas de análise estática** com **revisão por agentes de IA**.

Decisões (12/10/2026):
- Ferramentas: as **suítes do paper** (baseline comparável) **e** um conjunto **novo de regras
  Semgrep focadas em envelhecimento**.
- Integração: ferramentas e IA rodam como **canais independentes**. Um agente de triagem cruza as
  duas fontes e registra a origem de cada achado.
- Revisores de IA: os **subagentes VoltAgent** do paper (instalados no Claude Code).
- Escopo: **todas as 30 apps** (BaxBench Python, paper Python/JS/Rust, open-source), começando
  por **uma** (Uptime FastAPI).
- Regras de aging: derivadas de uma taxonomia da literatura **antes** de olhar os achados de cada
  app, com casos de teste para cada regra.
- Revisão com IA: **3 repetições por app** (variabilidade do LLM).
- Roda na máquina de desenvolvimento, porque é análise estática e não precisa da máquina isolada.

## Arquitetura

```
                 corpus/ (30 apps, cópias só de leitura + manifest.yaml)
                 ┌──────────────┴───────────────┐
       CANAL FERRAMENTAS (determinístico)    CANAL IA (VoltAgent, 3 repetições)
       ├ suítes do paper                     Fase 1: code-reviewer ∥ especialista da linguagem
       │  CodeQL · Semgrep · SlowQL · LeakAudit
       └ regras de aging (Semgrep)           Fase 2: performance-engineer (critério de 4 elementos)
                 │  tool_findings.json              │  ai_review_r{1,2,3}.md
                 └──────────────┬───────────────────┘
                     [aging-finding-triage]
                                │
                     findings/<app>-<n>.yaml (fila de validação) · identification/<app>/triage.md
```

## Fases

| # | fase | saída | status |
|---|---|---|---|
| I0 | Corpus (30 apps, BaxBench do git HEAD), ferramentas, plugins VoltAgent | `corpus/`, `static/install_tools.sh` | ✅ (falta o LeakAudit) |
| I1 | Suítes do paper. Sanidade: as contagens batem com `code-review-reports/` | `identification/<app>/tools/*.sarif`, `tool_findings.json` | ✅ piloto (bax-fastapi-uptime) |
| — | ⏸ checkpoint | | |
| I2 | Regras de aging (taxonomia → regras → testes → execução) | `static/rules/aging/` | |
| — | ⏸ checkpoint: revisão das regras | | |
| I3 | Revisão com IA (prompt de 2 fases do paper, `claude -p` isolado), piloto Uptime FastAPI × 3 | `identification/<app>/ai_review_r*.md` | |
| I4 | Agente de triagem: piloto comparado com o card manual | `findings/*.yaml`, `triage.md` | |
| — | ⏸ checkpoint | | |
| I5 | Expandir para as 30 apps e consolidar a contribuição de cada fonte | `identification/REPORT.md` | |

## Ameaças à validade (para registrar no paper)
- As regras de aging foram escritas por quem conhece o corpus. Mitigação: derivar da literatura
  antes, registrar a origem de cada regra e testar em casos sintéticos.
- O LLM não é determinístico. Mitigação: 3 repetições e medição da concordância entre elas.
- As versões das ferramentas mudam os resultados. Mitigação: fixar e registrar as versões
  (paper: Semgrep 1.168.0, CodeQL 2.25.4).

## Pendências
- Origem do **LeakAudit** (não está no PyPI).

## Registro

- 12/10: corpus com 30 apps (`identification/build_corpus.py`). Ficaram de fora `.env`, bancos de
  dados, `node_modules` e lockfiles. O BaxBench saiu do git HEAD (`bax-fastapi-uptime` é idêntico a
  `apps/uptime-fastapi/original`).
- 12/10: ferramentas nas versões do paper: Semgrep 1.168.0, CodeQL 2.25.4 (python-queries 1.8.2,
  javascript-queries 2.3.9, rust-queries 0.1.34), GitHubSecurityLab Community Packs **0.2.1** (a
  versão atual, 0.8.2, exige o CLI 2.27 e é incompatível). SlowQL 1.6.7 (versão do paper desconhecida).
- 12/10: plugins VoltAgent `voltagent-qa-sec` 1.1.2 e `voltagent-lang` 1.0.4 instalados no escopo do
  projeto (`.claude/settings.json`). Os subagentes têm Write/Edit/Bash; o modo só leitura do paper
  dependia apenas do prompt. Aqui ele será imposto via `--disallowedTools`.
- 12/10: **sanidade da I1 (bax-fastapi-uptime):** as 7 suítes de CodeQL/Semgrep reproduzem o paper
  regra por regra (security-experimental 4, community-audit 2, demais 0). O **SlowQL diverge**
  (paper: 2 consultas e 0 problemas; agora: 4 consultas e 7 problemas, todos de qualidade, custo ou
  esquema). A causa provável é a versão. Nenhum achado de ferramenta trata de envelhecimento.
