# 1. Visão geral

## Contexto

O trabalho anterior (*"Software Aging in LLM-Generated Applications: Runtime Evidence, Static
Analysis, and Human-Written Comparisons"*, de Santos, Vitagliano, Natella e Andrade) fez três coisas:

1. gerou aplicações backend com LLMs (cenários do BaxBench: Image Converter, Credit Card,
   Monitor e Uptime, em JavaScript, Python e Rust);
2. rodou cada uma por **48 h** com JMeter, medindo memória, tempo de resposta e vazão, e procurou
   tendências com Mann-Kendall e Sen's slope;
3. fez uma **análise estática** (CodeQL, Semgrep, SlowQL, LeakAudit e um fluxo de revisão com
   Claude Code), que classificou cada achado como *aging mechanism supported*, *conditionally
   plausible* ou *non-aging*.

Depois disso, foi criado um **agente de reparo** (`aging-repair-agent` + revisor + validador
funcional) que corrige um achado de cada vez.

## O problema

A análise estática aponta **mecanismos plausíveis**, mas não prova que eles se manifestam. Muitos
achados nunca foram exercitados pelo workload de 48 h. No Uptime, o JMeter usava só 4 serviços
fixos, então a tabela nunca passou de 4 linhas. Outros achados só aparecem em condições
específicas. O e-mail do Roberto cita alguns:

| achado condicional | o que é preciso para ativar |
|---|---|
| Monitor: travamento de processos externos | um processo externo que trava (ambiente) |
| Monitor: saturação do pool de threads | carga de alta intensidade (workload) |
| Uptime: vazamento de socket | runtime diferente do CPython (ambiente) |
| Uptime: estrutura de dados que cresce | acesso a chaves distintas (workload) |
| Uptime: falta de handler de shutdown | reinícios (ambiente) |

Sem uma relação causal (gatilho → acúmulo → sintoma), não dá para afirmar que o achado é
envelhecimento real nem avaliar se um reparo funcionou.

## O objetivo

Construir um **workflow agêntico** que, para cada achado estático:

1. **instrumenta** a aplicação para observar o caminho suspeito e o recurso que cresce;
2. **gera um workload (ou ambiente) direcionado** para disparar o gatilho;
3. **executa** e decide se o achado foi **ativado** e se causou **sintomas de envelhecimento**;
4. se não foi ativado, **revisa** o workload (loop com limite de tentativas) ou marca o achado
   como não controlável e passa para o próximo;
5. se foi ativado, **repara** (agente LLM), **valida a funcionalidade** e **reexecuta** o mesmo
   workload para comparar antes e depois.

Esse objetivo foi combinado entre Ermeson e Roberto (e-mails de 21 a 25/08/2026) como tema de um
paper dedicado. O primeiro passo é um **piloto manual**, este projeto, com um achado controlável
do Uptime. A implementação num framework agêntico (VoltAgent) fica para depois que o piloto
mostrar que a ideia é viável.

## O workflow

```
                  Aplicação gerada por LLM
                 ┌──────────┴───────────┐
          Workload fixo (paper)    Análise estática com IA
                 │                      │
         Avaliação de aging        Achados → Classificação
         (48 h, tendências)    ┌─────────┼──────────────┐
                           suportados condicionais   não-aging → documentar
                               └────┬────┘
                                    ▼
                  [instrumentation agent]  instrumentação dos caminhos suspeitos
                                    ▼
                  [workload generator]     workload / ambiente direcionado  ◄──┐
                                    ▼                                          │ revisar
                  [harness]                execução diagnóstica                 │ (≤ 3x)
                                    ▼                                          │
                  [activation judge]       foi ativado? ── não ────────────────┘
                                    │ sim                 (ou: não controlável → próximo achado)
                                    ▼
                  [repair agent ⇄ reviewer] reparo
                                    ▼
                  [functional validator]   validação funcional
                                    ▼
                  [harness]                reexecução com o MESMO workload
                                    ▼
                  [activation judge]       eficácia do reparo (antes × depois)
```

## O que este piloto quer responder

1. **Viabilidade:** um agente consegue gerar um workload que ativa um achado estático específico?
2. **Observabilidade:** a instrumentação gerada por agente basta para confirmar a ativação, sem
   precisar de cobertura de código ou de ferramentas externas de observabilidade?
3. **Causalidade:** o sintoma aparece no workload direcionado e não nos controles?
4. **Reparo:** o patch do agente remove o sintoma sob o mesmo workload, e a que custo?
5. **Automação:** em que pontos o humano precisou intervir? Isso orienta a versão em VoltAgent.

## Glossário

| termo | significado aqui |
|---|---|
| **Envelhecimento de software (aging)** | degradação progressiva de desempenho ou recursos com o tempo de execução (memória, latência, vazão), causada por acúmulo de estado ou recursos |
| **Achado (finding)** | item do relatório de análise estática que descreve um mecanismo suspeito |
| **Finding card** | YAML em `findings/` que formaliza o achado: recurso, gatilho, critérios de ativação e sintomas esperados |
| **Gatilho (trigger)** | o estímulo que faz o recurso crescer (aqui: serviceIds distintos) |
| **Ativação** | evidência em execução de que o caminho suspeito rodou **e** o recurso cresceu |
| **Sintoma** | métrica com tendência degradante significativa e relevante (ver [04](04-harness-e-estatistica.md)) |
| **Probe** | código de medição inserido pelo agente de instrumentação (`# AGING-PROBE`) |
| **Controle** | execução que **não** deveria ativar o achado; serve de contraste causal |
| **Execução diagnóstica (run)** | uma execução app + workload + monitoramento, com DB novo, guardada em `runs/` |
| **Harness** | os scripts determinísticos (sem LLM) que executam, coletam e analisam |
| **Controlável / não controlável** | se o gatilho pode ser produzido pelo workload ou ambiente que dominamos |
