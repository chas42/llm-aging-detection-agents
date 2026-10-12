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
| I0 | Corpus (30 apps, BaxBench do git HEAD), ferramentas, plugins VoltAgent | `corpus/`, `static/install_tools.sh` | ✅ |
| I1 | Suítes do paper. Sanidade: as contagens batem com `code-review-reports/` | `identification/<app>/tools/*.sarif.gz`, `tool_findings.json`, `identification/SANITY.md` | ✅ 30 apps (falta CodeQL em 2 apps Express: `libatomic1`) |
| — | ⏸ checkpoint | | |
| I2 | Regras de aging (taxonomia → regras → testes → execução) | `static/rules/aging/`, `identification/TOOLS_SUMMARY.md` | ✅ v1.0 congelada, rodada nas 30 apps |
| — | ⏸ checkpoint: revisão das regras | | |
| I3 | Revisão com IA (prompt de 2 fases do paper, `claude -p` isolado), piloto Uptime FastAPI × 3 | `identification/<app>/ai_review_r*.md` | ✅ piloto: 3 repetições válidas (~US$ 1 cada, uso estimado) |
| I4 | Agente de triagem: piloto comparado com o card manual | `identification/<app>/triage/`, `COMPARISON.md` | ✅ piloto: 1 card, igual ao manual no mecanismo; classificação conditional |
| — | ⏸ checkpoint | | |
| I5 | Expandir para as 30 apps e consolidar a contribuição de cada fonte | `identification/REPORT.md` | |

## Uso da conta (importante)

As revisões e a triagem rodam como sessões do Claude Code (`claude -p`) com a **conta do claude.ai**
logada nesta máquina, sem chave de API. O `total_cost_usd` registrado nos JSONs é uma **estimativa
equivalente em preço de API**, não uma cobrança: o uso consome o **limite do plano** (o mesmo das
conversas). Rodar muitas apps de uma vez pode esgotar o limite por algumas horas, por isso a
expansão deve ser feita em **lotes aprovados**. Referência do piloto: ~1 por revisão e ~1 por
triagem; 30 apps × (3 revisões + triagem) ≈ 120.

## Ameaças à validade (para registrar no paper)
- As regras de aging foram escritas por quem conhece o corpus. Mitigação: derivar da literatura
  antes, registrar a origem de cada regra e testar em casos sintéticos.
- O LLM não é determinístico. Mitigação: 3 repetições e medição da concordância entre elas.
- As versões das ferramentas mudam os resultados. Mitigação: fixar e registrar as versões
  (paper: Semgrep 1.168.0, CodeQL 2.25.4).

## Pendências
- `sudo apt install libatomic1`: o Node.js do sistema precisa dela para o CodeQL extrair TypeScript
  (apps `paper-express-uptime` e `paper-express-image-converter`, que têm frontend Vite/TS).

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
- 12/10: **LeakAudit** instalado de https://github.com/kriskimmerle/leakaudit no commit `36edccd`
  (v0.1.0, como no paper), depois de revisar o código (só análise sintática, sem rede nem execução).
  Reproduz o paper (`dbconn-no-with` no Flask Uptime).
- 12/10: **regras de aging v1.0**: 23 regras (Python 9, JS 8, Rust 6), 92 testes passando, congeladas
  (`static/rules/aging/RULES.sha256`) antes de rodar no corpus. Limitações do Semgrep CE 1.168
  contornadas: metavariáveis não exportados no JSON (são lidos de tags na mensagem), `pattern-inside`
  com atribuição não casa com `X[k] = v`, e comentários satisfaziam as condições de arquivo.
- 12/10: **sanidade da I1 nas 30 apps** (`identification/SANITY.md`):
  - CodeQL: 107 de 111 idênticos. As 4 diferenças são do Django Monitor: o banco do paper incluía
    uma app `monitor/` que nunca foi commitada (cópia de trabalho diferente; não reproduzível).
  - Semgrep: 20 de 26 idênticos. As diferenças vêm da deriva do registry (`p/default` e
    `p/security-audit` não têm versão). O `p/flask` bate 4 de 4.
  - SlowQL: 1 de 13 (versão diferente da do paper).
  - Os rulesets do registry foram congelados em `static/rules/registry/` (snapshot de 12/10) e são
    usados por padrão a partir de agora; verificado que dão resultados idênticos ao registry ao vivo.
- 12/10: **corpus inteiro** (`identification/TOOLS_SUMMARY.md`): 29 achados de aging e 4 de vazamento de
  recurso (LeakAudit), entre 356 achados no total. As suítes do paper não encontram nenhum mecanismo
  de aging. As regras novas marcam os mecanismos que o Roberto citou: crescimento por chaves
  distintas nos 6 Uptime com SQL/HashMap e "processo externo sem timeout" nos Monitor e Image
  Converter. Dois falsos negativos conhecidos foram registrados em `TAXONOMY.md`, sem corrigir a v1.0.
- 12/10: **I3 piloto (bax-fastapi-uptime)**. A 1ª tentativa falhou porque plugins do escopo de projeto
  não carregam fora da pasta do projeto. Correção: `--plugin-dir` com o cache local, mais validação
  estrutural da saída. 3 repetições válidas (uso estimado equivalente a US$ 0,92–1,04 em API, ~3 min cada). O mecanismo principal
  aparece 3/3, com veredicto oscilando (1 supported, 2 conditional).
- 12/10: **I4 piloto**. A triagem isolada (uso estimado equivalente a US$ 1,11) gerou 1 card que coincide com o manual no
  mecanismo, gatilho, código, controlabilidade e sintomas. Classificou como *conditional* (o manual,
  seguindo o paper, diz supported). Ela também propôs por conta própria o controle pareado (mesma
  taxa, chaves fixas) e apontou o plano de execução do SELECT como variável a registrar. Detalhes em
  `identification/bax-fastapi-uptime/COMPARISON.md`.
