# Piloto I3/I4: card gerado pela triagem × card manual

- **Manual:** `findings/uptime-fastapi-F4.yaml`, escrito à mão em 04/10 a partir do relatório do paper.
- **Automático:** `identification/bax-fastapi-uptime/triage/bax-fastapi-uptime-F1.yaml`. Gerado pelo
  agente de triagem isolado a partir de 3 revisões de IA (VoltAgent, prompt do paper) e dos achados
  das ferramentas, **sem acesso** ao card manual nem ao relatório do paper.

## Uso e execução (12/10/2026)

> **Sobre os valores em US$:** são o *uso estimado equivalente em API* que o Claude Code calcula
> (`total_cost_usd`). As execuções usam a conta do claude.ai (assinatura), então **não são cobradas
> em dinheiro**: consomem o limite de uso do plano. Só haveria cobrança se o uso extra (além do
> limite) estivesse ativado na conta.

| etapa | duração | uso estimado (equiv. API) | observação |
|---|---|---|---|
| revisão r1 (1ª tentativa) | 11 s | US$ 0,26 | inválida: plugins não carregados fora da pasta do projeto; o orquestrador parou sem improvisar |
| revisão r1 | 201 s | US$ 1,04 | válida |
| revisão r2 | 201 s | US$ 0,98 | válida |
| revisão r3 | 186 s | US$ 0,92 | válida |
| triagem | 163 s | US$ 1,11 | 1 card, 17 candidatos documentados como non-aging/unsupported |
| **total** | | **US$ 4,31** | orquestrador Opus 5.5; subagentes VoltAgent em Sonnet 5.5 |

## Estabilidade entre as 3 revisões

| mecanismo | r1 | r2 | r3 |
|---|---|---|---|
| crescimento de linhas (+ `/services` sem limite) | supported (+ conditional, separado) | conditional (juntos) | conditional (juntos) |
| lock global + conexão por requisição | non-aging | non-aging | non-aging |
| SQLite com vários processos | fora de aging | non-aging | non-aging |
| erros de parsing de timestamp | fora de performance | fora de performance | fora de performance |

O mecanismo principal é encontrado 3 de 3 vezes, mas o **veredicto oscila** (1 supported, 2 conditional).
O motivo do "conditional" é que o crescimento depende de o tráfego criar chaves *distintas*, como o
Roberto descreveu esse caso no e-mail de 21/08.

## Comparação campo a campo

| campo | manual | automático | avaliação |
|---|---|---|---|
| mecanismo | F4 (escrita) + F5 (leitura) numa cadeia | o mesmo, numa cadeia | ✅ igual |
| classificação | supported (seguindo o paper) | **conditional** (precisa de chaves distintas) | ⚠️ diverge; o automático está alinhado com a maioria das revisões e com o e-mail do Roberto |
| recurso | linhas por token, tamanho do DB | o mesmo + índices B-tree | ✅ igual, mais detalhado |
| gatilho | serviceIds distintos | o mesmo + "o mesmo token" para o lado da leitura | ✅ igual |
| código suspeito | upsert_heartbeat, list_services_for_token, services | os mesmos + HeartbeatIn (sem limite de tamanho) + init_db | ✅ superconjunto |
| controlabilidade | workload | workload | ✅ igual |
| controle | o `.jmx` com 4 chaves fixas não ativa | **"conjunto fixo de pares na MESMA taxa"** | ✅ chega por conta própria ao controle pareado (R0b), que foi desenhado à mão depois do checkpoint 1 |
| critério de crescimento | MK crescente + razão ≥ 10× | crescimento monotônico, "várias vezes" em 10⁴–10⁵ pares | ≈ menos quantitativo |
| sintomas esperados | latência/tamanho de `/services`, DB, vazão e lock, latência do heartbeat, RSS improvável | os mesmos, na mesma ordem + picos transitórios de RSS + CPU por requisição | ✅ igual, mais rico |
| prioridade | — | 2 (nenhum supported, então não há prioridade 1) | — |

## O que o card automático trouxe e o manual não tinha

1. **Plano de execução do SELECT:** se o SQLite usar o índice de `token`, o custo cresce só com as linhas
   do token consultado. Se fizer scan da PK, novos tokens também degradam os outros. Isso afeta a
   leitura dos **tokens de referência** do workload (controle interno). Vale registrar o
   `EXPLAIN QUERY PLAN` na instrumentação.
2. **Tamanho dos campos sem limite**, que agrava o crescimento em bytes por linha.
3. **Um único processo uvicorn**, para não confundir com a contenção de múltiplos processos (non-aging).

## Contribuição das fontes (este app)

| fonte | achou o mecanismo? |
|---|---|
| revisões de IA | 3/3 |
| regras de aging v1.0 | sim (2 regras: INSERT sem retenção e SELECT sem LIMIT) |
| suítes do paper | só indiretamente: o SlowQL PERF-SCAN-003 aponta o scan; CodeQL e Semgrep não |
| LeakAudit | não |

## Conclusão do piloto

A etapa de identificação reproduziu, sem intervenção humana e sem ver o gabarito, o card que hoje
alimenta a validação dinâmica, com uma classificação mais conservadora e três observações úteis.
Ressalva: as regras de aging foram escritas por quem já conhecia esse relatório (ver
`static/rules/aging/TAXONOMY.md`); para medir as ferramentas, as apps JS/Rust/open-source são mais
confiáveis.
