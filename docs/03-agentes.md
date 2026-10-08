# 3. Agentes

As definições completas ficam em [`.claude/agents/`](../.claude/agents/). Cada arquivo `.md` é o
prompt de sistema de um subagente do Claude Code. O frontmatter define nome, descrição e
ferramentas permitidas. Este documento resume cada um.

## Como invocar

- **Sessão do Claude Code aberta em `aging-validation/`:** os agentes são carregados
  automaticamente. Basta pedir, por exemplo: *"rode o aging-activation-judge sobre as execuções
  R1, R0b e R0"*.
- **Sessão aberta em outra pasta (como até agora):** o Claude lança um agente genérico com a
  instrução "leia `.claude/agents/<nome>.md` e siga como instruções", mais as entradas. O
  resultado nos arquivos é o mesmo.

Todos leem `CLAUDE.md` e a finding card. Todos terminam com um **relatório em formato fixo**, o
que facilita a automação futura.

## Visão geral

| agente | fase | entrada | saída | ferramentas | pode editar código? |
|---|---|---|---|---|---|
| `aging-instrumentation-agent` | 1 | finding card, app | `apps/<app>/instrumented/` | Read, Grep, Glob, Write, Edit, Bash | só a cópia |
| `aging-workload-generator` | 2 (e loop) | finding card, API, `.jmx` original, veredito (revisão) | `workloads/*.py` + `.md` | Read, Grep, Glob, Write, Edit, Bash | não toca na app |
| `aging-activation-judge` | 4 e 6 | finding card, pastas de execução | `verdict.md` | Read, Grep, Glob, Bash, Write | não |
| `aging-repair-agent` | 5 | finding card (+ veredito), app instrumentada | `apps/<app>/repaired/` | Read, Grep, Glob, Edit, Write | só a cópia |
| `aging-repair-reviewer` | 5 | finding card, diff | APPROVE / REQUEST_CHANGES / REJECT | Read, Grep, Glob | não |
| `aging-functional-validator` | 1, 5 | app, teste | PASS / FAIL / SETUP_FAILED | Bash, Read, Grep, Glob | não |

---

## `aging-instrumentation-agent` (novo)

**Papel:** tornar o achado *observável*, sem corrigi-lo nem mudar o comportamento da app.
Implementa a sugestão do Roberto de um "agente de instrumentação" que registra a execução do
código suspeito e evita depender de cobertura de código ou de ferramentas externas.

**Regras principais**
- Copia a app e edita só a cópia. Toda a lógica fica em `aging_probe.py`; no app entram só
  ganchos marcados com `# AGING-PROBE`.
- Nada roda sem `AGING_INSTRUMENT=1`. Exceções das probes nunca chegam ao handler.
- Snapshots periódicos (thread daemon) com:
  - contadores de chamada dos caminhos suspeitos;
  - medidores do recurso (linhas, tamanho do DB);
  - máximos por janela (espera e tempo no lock, linhas retornadas).
- As probes não acumulam estado e têm custo limitado e documentado.
- Injeção de gatilhos (atrasos, travamentos simulados) **só** quando
  `controllability.level: environment`, por exemplo nos achados condicionais do Monitor.

**Autoverificação:** `py_compile`, teste funcional com probes desligadas e ligadas, e um `diff`
confirmando que toda linha alterada tem a marca.

## `aging-workload-generator` (novo)

**Papel:** escrever a carga que exercita **exatamente o gatilho** do achado, numa taxa que mostre
acúmulo em 1–2 h, usando a API de forma válida.

**Regras principais**
- Mirar no gatilho, não em volume. Se o gatilho exige chaves distintas, o espaço de chaves deve
  crescer de forma monotônica e controlada.
- Sondas de leitura com taxa e parâmetros fixos para medir o sintoma de forma comparável ao
  longo do tempo.
- Ritmo explícito para não saturar o servidor desde o início (queremos degradação causada pelo
  crescimento, não pela saturação).
- Estimar o tamanho do recurso ao fim da execução.
- Gerar também o **controle** fiel ao workload original.
- **Modo revisão:** muda só o que o juiz apontou, cria uma nova versão (`_v2`, …) e mantém a
  anterior. Se o gatilho for inalcançável pela API, diz isso em vez de aumentar a carga às cegas.

**Autoverificação:** `--help` e uma execução curta de 60 s pelo harness.

## `aging-activation-judge` (novo)

**Papel:** decidir, só com evidência de execução, se o achado foi ativado e se causou
envelhecimento. Também avalia a eficácia do reparo.

**Processo:**
1. roda `analyze_run.py`;
2. verifica se o caminho foi executado (probes);
3. verifica o crescimento do recurso (critério da card);
4. compara com o controle;
5. verifica os sintomas pelos critérios (a)–(d) de [04](04-harness-e-estatistica.md#critério-de-sintoma);
6. descarta explicações alternativas: cliente saturado, efeitos do host, aquecimento,
   overhead das probes.

**Vereditos**
- Ativação: `ACTIVATED_WITH_AGING`, `ACTIVATED_NO_SYMPTOM`, `NOT_ACTIVATED`, `INCONCLUSIVE`.
- Reparo: `REPAIR_EFFECTIVE`, `REPAIR_PARTIAL`, `REPAIR_INEFFECTIVE`, mais as regressões
  introduzidas.

**Loop:** com `NOT_ACTIVATED` e tentativas restantes, devolve de 1 a 3 mudanças concretas de
parâmetro para o gerador de workload. Sem tentativas restantes, recomenda `NOT_CONTROLLABLE` e
passar ao próximo achado.

## Agentes de reparo (portados de `baxbench_LLM_code`)

Os três agentes que já existiam foram **copiados** (os originais continuam intactos) com estes
ajustes:

| agente | o que mudou na cópia |
|---|---|
| `aging-repair-agent` | recebe a finding card (e o veredito do juiz); aplica o patch numa **cópia** (`repaired/`) da app **instrumentada**; não pode alterar `# AGING-PROBE` nem `aging_probe.py` |
| `aging-repair-reviewer` | novo critério: qualquer mudança em linhas de probe é `REQUEST_CHANGES` (invalidaria a comparação antes/depois) |
| `aging-functional-validator` | usa `reference/tests/` e o helper determinístico `harness/functional_check.py` |

O resto continua igual: patch mínimo, sem mudar a API, um achado por vez, revisão adversarial e
validação sem consertar nada.

## O que fica com o humano no piloto

- escrever e classificar a finding card;
- os pontos de checagem (instrumentação e workload; resultado de ativação; comparação final);
- rodar a campanha na máquina isolada ([deploy/README.md](../deploy/README.md));
- julgar se as decisões dos agentes fazem sentido. Isso alimenta a resposta à pergunta
  "o que dá para automatizar?" no paper.
