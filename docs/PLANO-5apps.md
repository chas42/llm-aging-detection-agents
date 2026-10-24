# Plano: 5 apps, identificação + validação completa (paper de journal)

Contexto: e-mails de 15–16/10/2026. O Ermeson propôs 3 aplicações com execuções de 8 h; o Roberto
respondeu que 3–4 apps bastam, podendo crescer conforme o feedback do paper TRel. Decisão de
17/10: **validar 5 apps** (Uptime do piloto + 4 novas), **todas as execuções com 8 h**, e
identificação completa com IA (3 revisões + triagem) nas apps novas.

## Apps e problema-alvo

| app | variante (corpus) | problema-alvo | como disparar | adaptação |
|---|---|---|---|---|
| Uptime | `bax-fastapi-uptime` | linhas sem limite no banco (disco) | chaves novas | nenhuma (refazer com 8 h) |
| Monitor | `bax-flask-monitor` | `ps` sem timeout | ambiente: travamento do `ps` injetado pela instrumentação | iniciar Flask (WSGI) |
| Image Converter | `bax-flask-image-converter` | `convert` sem timeout + arquivos temporários | entradas pesadas | Flask + ImageMagick no servidor |
| Credit Card | `bax-fastapi-credit-card` | INSERT sem retenção | cartões e telefones distintos | nenhuma |
| Uptime Rust | `paper-actix-uptime` | `HashMap` em memória sem limite | chaves novas | compilar/iniciar binário Rust (Rust no servidor) |

O FastAPI Monitor lê `/proc` diretamente (sem subprocess), por isso a variante Flask.

## Etapas

| # | etapa | onde | status |
|---|---|---|---|
| 1 | Identificação: 3 revisões + triagem nas 4 apps novas | aqui | ✅ 16/16 válidas, ~US$ 16 equiv. |
| — | ⏸ checkpoint: confirmar o problema-alvo de cada app | Cesar | ✅ os 5 F1 |
| 2 | Harness genérico (FastAPI, Flask, Rust), instrumentação, workloads (R1 + controles R0/R0b), testes locais, bundle | aqui | ✅ ensaio de 15 execuções OK |
| — | ⏸ checkpoint antes das execuções longas | Cesar | ✅ 2 chaves/s nos Uptime; um bloco por app |
| 3 | Campanha 1: R0, R0b, R1 × 5 apps, 8 h cada (~120 h) | 2 PCs | |
| 4 | Juiz → reparo → revisor → teste funcional (apps ativadas) | aqui | |
| 5 | Campanha 2: R2 das apps reparadas, 8 h cada (até ~40 h) | 2 PCs | |
| 6 | Consolidação: tabela por app, uso de tokens, problemas do fluxo | aqui | |

## Requisitos no servidor
- `sudo apt install imagemagick`
- Rust via rustup, para compilar o Actix no próprio servidor. Um binário compilado aqui não serve:
  aqui a glibc é 2.39, e no Ubuntu 22.04 é 2.35.

## Riscos
- Monitor: a injeção de travamento muda o ambiente. Ela precisa ser descrita no paper, e o R0b
  (sem injeção) precisa ficar estável.
- Image Converter: o tamanho das entradas define o tempo do `convert`. Pode precisar de calibração no loop.
- Credit Card: provável crescimento "por design". Se o juiz concluir que não é envelhecimento, não
  há reparo nem R2.

## Registro
- 17/10: **identificação das 4 apps** (16 sessões, todas válidas, ~US$ 15,91 equivalentes). Cards F1:
  Uptime Rust *supported* (prioridade 1, carga); Credit Card *conditional* (carga); Image Converter e
  Monitor *conditional* com gatilho de **ambiente**. Um card do Credit Card saiu com YAML inválido
  (valor com `: ` sem aspas): foi corrigido à mão e documentado em `run.json`, e a triagem agora valida
  todos os cards.
- 17/10: **injeção nas apps de ambiente**: no Monitor, a injeção é feita pelo agente de instrumentação,
  no código e ligada por variável; no Image Converter, por um **shim no PATH**, sem mudar o código. Os
  dois casos ficam comparáveis no paper.
- 17/10: **harness generalizado**: `app.yaml` por app (FastAPI e Flask iniciam pelo próprio `python
  app.py`, como no paper; o Rust compila uma vez e roda o binário), ambiente por execução
  (`--app-env`), `deploy/campaign.py` para várias apps com um pacote de resultados por app, e preflight
  e `setup.sh` para todas as apps. Limites de arquivos abertos e de processos elevados e registrados.
- 17/10: o teste funcional do Image Converter no paper **não roda como está** (caminho e porta fixos) e,
  pela especificação da API, interpreta o `delay` errado. Ele foi adaptado em `tests/functional/`. A
  própria app **diverge da especificação** em `appendReverted` (3 quadros em vez de 4). A validação do
  reparo dessa app será diferencial: mesma saída que a original.
- 17/10: **problemas do fluxo agêntico observados** (para o Roberto):
  1. Um agente de instrumentação deixou um servidor de teste **órfão** na porta 5000 por ~5 h, o que
     bloqueou o agente seguinte (que corretamente não o matou). Os prompts agora exigem encerrar
     qualquer processo iniciado e preferir os scripts do harness.
  2. As apps Python usam todas a porta 5000, então os agentes delas precisam rodar **em série**.
  3. A sessão atingiu o **limite de uso do plano** no meio de um agente. Ele foi retomado depois do
     reset, sem perda de trabalho.
  4. Os agentes **se desviaram do brief com boas razões, e documentaram isso**: o travamento do `ps`
     ficou em 10 h em vez de 10 min (com 10 min seria um degrau, não uma tendência); os testes do
     Credit Card rodaram numa porta alternativa por causa do servidor órfão.
- 17/10: **taxa do Uptime para 8 h** (decidido em 18/10: **2 chaves/s**): a 10 chaves/s, a leitura de `/services` chega a
  ~3,8 s e satura o lock por volta de 4 h; a 2/s, termina em ~0,8 s sem saturar.
- 18/10: as 5 apps prontas. Monitor e Image Converter instrumentados; o Image Converter usa um **shim no
  PATH** (`env/shims/convert-hang`), transparente com p=0 (GIF idêntico byte a byte) e com decisões
  reproduzíveis e registradas em log. O preflight do servidor reconhece a falha funcional
  pré-existente do Image Converter. **Ensaio da campanha completa** (5 apps × R0, R0b, R1, 30 s): 15/15
  sem falha; o shim e a injeção só ficam ativos na R1. **Calendário: um bloco por app (~24 h cada)**, na
  ordem de prioridade dos cards.
- 22/10: **campanha 1 em andamento**. A ordem mudou: o bloco do `uptime-fastapi` rodou primeiro, e o
  bloco do `uptime-actix` (Rust) ficou **adiado**. Problemas operacionais desta fase: o cliente desligou
  no meio de uma execução, e a campanha chegou a rodar fora do tmux (queda do SSH = execução perdida).
  Procedimento de recuperação: encerrar o agente do servidor (`Ctrl-C` no tmux), descartar a execução
  interrompida e refazer só as que faltam com `RUNS=...`. Também ficou para trás um agente de teste
  local (127.0.0.1:9000) de um comando cancelado em 18/10; encerrado em 22/10, sem efeito nos PCs.
- 22–24/10: **bloco uptime-fastapi (8 h, 2 chaves/s): ACTIVATED_WITH_AGING** (`docs/verdicts/uptime-fastapi-R1-8h.md`).
  Linhas por token 200 → 57.590; `/services` p50 9,9 → 725 ms; resposta 16 KB → 5,2 MB; RSS 54 → 145 MB e
  ainda crescendo (acompanha a maior resposta, r = 0,992, mesma inclinação do piloto); heartbeat p95
  37,6 → 247 ms. Sem saturação (0 erros em 216 mil requisições). Os outros tokens são afetados pela
  sobreposição com a leitura grande (lock global, GIL, serialização), não pelo plano da consulta, que
  usa o índice. Um travamento único de 2,5 s às 5,34 h no servidor, de causa desconhecida.
- 24/10: **bloco credit-card-fastapi (8 h): ACTIVATED_WITH_AGING, subclassificação MISTA**
  (`docs/verdicts/credit-card-fastapi-R1-8h.md`). Por design: linhas, banco e tamanho da resposta (a spec
  exige devolver todos os cartões). Envelhecimento: travamento de ~150 ms do processo inteiro ao
  serializar respostas grandes (afeta outros telefones e as gravações), custo por cartão ~2,7× o
  necessário e pico de memória crescente (54 → 113 MB). Leitura do telefone quente 9,8 → 1.270 ms (o
  servidor é ~2,7× mais lento que a máquina da calibração). Reparo sem mudar a spec: só **parcial**.
  Limitar de verdade exige decisão de especificação (paginação ou limite por telefone).
