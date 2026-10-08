# Execução em ambiente isolado: tutorial

Este tutorial leva os experimentos da máquina de desenvolvimento para uma **máquina isolada**
(dedicada, sem outras cargas) e traz as evidências de volta para análise. Ele cobre a
**Fase 3** do [plano](../docs/PLANO.md). O julgamento de ativação (Fase 4) é feito depois, na
máquina de desenvolvimento, com os agentes.

```
 MÁQUINA DE DESENVOLVIMENTO                    MÁQUINA ISOLADA
 ──────────────────────────                    ───────────────
 1. deploy/pack.sh ──── bundle .tar.gz ──────► 3. deploy/setup.sh
                        (scp / pendrive)        4. deploy/preflight.sh       (~3 min)
                                                5. deploy/campaign_uptime_F4.sh  (~3h10)
 8. agentes: activation judge ◄── results ──── 6/7. deploy/collect_results.sh (automático)
```

---

## 0. Requisitos da máquina isolada

| item | mínimo | recomendado |
|---|---|---|
| SO | Linux x86_64 | Ubuntu 22.04 ou 24.04 **nativo** ou VM dedicada (não WSL, não laptop em bateria) |
| CPUs | 4 | ≥ 4 núcleos físicos (servidor na CPU 0, carga nas CPUs 1–3) |
| RAM | 2 GB livres | 4 GB |
| Disco | 2 GB livres | SSD local (a latência do `/heartbeat` é dominada por `fsync`) |
| Python | 3.10 | 3.10–3.12, com `python3-venv` instalado |
| Rede | internet para o `pip` **ou** bundle offline (`--wheels`) | sem tráfego concorrente |
| Outros | `taskset` (pacote `util-linux`), `tar`, `sha256sum` | `tmux` |

Não é preciso Docker, JMeter nem R. App, carga e coleta rodam todos na mesma máquina, em
`127.0.0.1`, separados por CPU com `taskset`.

> Por que não usar Docker? Os limites de cgroup e o overlayfs mudam os valores de memória e de I/O
> medidos. O paper original mediu direto no sistema operacional, e este piloto segue o mesmo padrão.

Instalação dos pré-requisitos no Ubuntu, se faltar algum:
```bash
sudo apt update && sudo apt install -y python3 python3-venv util-linux tmux
```

---

## 1. Gerar o bundle (máquina de desenvolvimento)

Na pasta `aging-validation/`:

```bash
bash deploy/pack.sh                 # máquina isolada COM internet
# ou
bash deploy/pack.sh --wheels 3.10   # máquina isolada SEM internet (use a versão do python3 -V de lá)
```

Saída: `dist/aging-validation-bundle-<data>.tar.gz` e o `.sha256` correspondente.
Tamanho: ~50 KB, ou ~60–80 MB com `--wheels`.

O bundle **contém**: as apps (`original` e `instrumented`), os workloads, o harness, a finding
card, as referências, a documentação e estes scripts.
O bundle **não contém**: `.venv`, `runs/` nem os agentes (`.claude/`), porque nenhum LLM roda na
máquina isolada.

## 2. Transferir

Copie **os dois arquivos** (`.tar.gz` e `.tar.gz.sha256`):

```bash
scp dist/aging-validation-bundle-<data>.tar.gz* usuario@maquina-isolada:~/
```

## 3. Instalar (máquina isolada)

```bash
cd ~
sha256sum -c aging-validation-bundle-<data>.tar.gz.sha256    # deve imprimir: OK
tar -xzf aging-validation-bundle-<data>.tar.gz
cd aging-validation-bundle-<data>
bash deploy/setup.sh
```

O `setup.sh`:
1. confere a integridade de cada arquivo (`MANIFEST.sha256`);
2. verifica se o Python é 3.10 ou mais novo;
3. cria `.venv/` (se o `python3-venv` faltar, contorna com `--without-pip`);
4. instala as dependências, da internet ou do `wheelhouse/` se ele existir.

Se a versão padrão for antiga, use outro Python: `PYTHON=/usr/bin/python3.11 bash deploy/setup.sh`.

## 4. Verificar o ambiente (preflight, ~3 min)

```bash
bash deploy/preflight.sh
```

Ele confere o ambiente (CPUs, portas 8000/3000, disco, RAM, carga do sistema, WSL, governor de
CPU e NTP), roda o **teste funcional** do BaxBench na app original e na instrumentada (com as
probes desligadas e ligadas) e faz uma **execução curta de 60 s** do workload direcionado.

- `[FAIL]`: **não** prossiga. Veja o log indicado e a seção [Problemas comuns](#problemas-comuns).
- `[WARN]`: pode prosseguir, mas registre o aviso. Para medições mais estáveis, vale corrigir:
  ```bash
  # governor de CPU em "performance" (volta ao padrão no reboot)
  echo performance | sudo tee /sys/devices/system/cpu/cpu*/cpufreq/scaling_governor
  # pare serviços pesados que não sejam necessários (atualizações automáticas, indexadores, etc.)
  sudo systemctl stop unattended-upgrades 2>/dev/null
  ```
- O relatório fica em `runs/_preflight_<ts>/report.txt` e volta junto com os resultados.

Com menos de 4 CPUs, rode tudo sem fixar CPU: `APP_CPUS= LOAD_CPUS= bash deploy/preflight.sh`,
e use as mesmas variáveis na campanha.

## 5. Rodar a campanha (~3h10)

Rode **dentro do tmux** (ou com `nohup`) para a campanha sobreviver a uma queda da conexão SSH:

```bash
tmux new -s aging
bash deploy/campaign_uptime_F4.sh
# sair do tmux sem parar a campanha: Ctrl-b e depois d      | voltar: tmux attach -t aging
```

A campanha roda, **em sequência** e sempre com um banco de dados novo, a app instrumentada com:

| execução | workload | o que prova |
|---|---|---|
| `R0_F4_control_jmx` | réplica fiel do `.jmx` original (4 serviços fixos, sem pausa) | o workload do paper **não** ativa o achado |
| `R0b_F4_matched_control` | direcionado com `--key-space 4` (mesma carga, sem chaves novas) | isola a variável "chaves distintas" |
| `R1_F4_targeted_v1` | direcionado (10 serviceIds novos/s + leituras a cada 2 s) | tenta **ativar** o achado e observar sintomas |

Entre uma execução e outra há 120 s de pausa. Ao final de cada execução, o `analyze_run.py` roda
automaticamente.

**Variáveis de configuração** (opcionais, colocadas antes do comando):

| variável | padrão | exemplo |
|---|---|---|
| `DURATION` | `3600` (s por execução) | `DURATION=7200` para 2 h |
| `RUNS` | `"R0 R0b R1"` | `RUNS="R1"` para refazer só a R1 |
| `APP_CPUS` / `LOAD_CPUS` | `0` / `1-3` | `APP_CPUS= LOAD_CPUS=` desliga a fixação de CPU |
| `SEED` | `42` | mantenha igual entre as execuções que serão comparadas |
| `COOLDOWN` | `120` | segundos entre execuções |

**Acompanhar** (de outro terminal):
```bash
tail -f runs/_campaign_*/campaign.log                 # início e fim de cada execução
tail -f runs/*_R1_F4_targeted_v1/workload.log         # progresso: chaves enviadas, requisições, erros
```

**Durante a campanha, não:** rode outros processos pesados, deixe a máquina suspender ou
hibernar, ou mexa em `runs/`.

**Interrompeu?** `Ctrl-C` no tmux para a execução atual, e o harness derruba o servidor. Para
retomar, rode só o que faltou, por exemplo `RUNS="R0b R1" bash deploy/campaign_uptime_F4.sh`. A
execução interrompida fica em `runs/` e deve ser descartada na análise.

## 6. Coletar os resultados

Ao terminar, a campanha chama `deploy/collect_results.sh` sozinha e gera:

```
results/aging-results-<host>-<ts>.tar.gz   (+ .sha256)
```

O pacote leva as 3 execuções, a pasta da campanha (`campaign.log`, `system.txt`, com a descrição
da máquina para a seção de setup do paper) e o último preflight. Para empacotar manualmente:
`bash deploy/collect_results.sh runs/_campaign_<ts>`, ou sem argumento para levar tudo o que
estiver em `runs/`.

## 7. Trazer de volta (máquina de desenvolvimento)

```bash
scp usuario@maquina-isolada:~/aging-validation-bundle-<data>/results/aging-results-* .
sha256sum -c aging-results-<host>-<ts>.tar.gz.sha256
tar -xzf aging-results-<host>-<ts>.tar.gz -C /home/cesar/projects/llm-research/aging-validation/
```

As execuções aparecem em `aging-validation/runs/`, com a mesma estrutura gerada localmente.

## 8. Próximo passo

Na máquina de desenvolvimento, com o Claude Code, peça a **Fase 4**: rodar o
`aging-activation-judge` sobre `R1` (direcionado), `R0b` e `R0` (controles). Veja
[docs/03-agentes.md](../docs/03-agentes.md).

---

## O que cada execução produz

```
runs/<ts>_<label>/
├── meta.json               parâmetros, hashes do código, início/fim, códigos de saída
├── monitor.csv             a cada 5 s: RSS, USS, VMS, CPU, threads, FDs, tamanho do DB
├── client.csv              uma linha por requisição: ts, endpoint, status, latência, bytes, fase
├── instrumentation.jsonl   a cada 10 s: contadores dos caminhos suspeitos e linhas na tabela
├── server.log / workload.log
├── workdir/                cópia da app usada + db.sqlite3 final (estado acumulado)
└── analysis/               summary.md/json (Mann-Kendall, Sen) + series.png
```

Tamanho esperado por execução de 1 h: client.csv com ~3 MB (R1) a ~25 MB (R0, que não tem pausa) e db.sqlite3 com até ~5 MB.

## Problemas comuns

| sintoma | causa provável | solução |
|---|---|---|
| `setup.sh`: `integrity` falha | bundle corrompido na cópia | copie de novo e confira o `.sha256` |
| `ensurepip is not available` | falta o `python3-venv` | `sudo apt install python3-venv`, ou deixe o fallback do setup agir |
| `pip` sem rede | máquina offline | gere o bundle com `--wheels <versão do python lá>` |
| preflight: `port 8000 in use` | outro serviço ou execução anterior viva | `ss -ltnp \| grep 8000` e encerre o processo, se for seu |
| preflight: `smoke run unhealthy` | app não sobe ou erros HTTP | veja `runs/_preflight_*/smoke_run/server.log` |
| `taskset: failed to set pid's affinity` | menos CPUs que o esperado ou restrição de cgroup | `APP_CPUS= LOAD_CPUS=` |
| `server did not start within 30s` | dependência faltando ou porta ocupada | `runs/<execução>/server.log` |
| `analysis` vazio com WARNING | execução muito curta (< 3 min) | normal em testes; nas execuções reais use `DURATION` ≥ 600 |

## Checklist

- [ ] máquina dedicada, sem outras cargas, alimentação na tomada
- [ ] `sha256sum -c` OK na ida **e** na volta
- [ ] `preflight.sh` sem `[FAIL]` (avisos anotados)
- [ ] campanha rodando no tmux/nohup
- [ ] `results/aging-results-*.tar.gz` copiado e extraído em `aging-validation/runs/`
