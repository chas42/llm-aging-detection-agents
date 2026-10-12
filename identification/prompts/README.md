# Prompts da revisão com IA (I3)

Um template por framework, com `{target}` no lugar da pasta revisada.

| framework | origem | especialista (Fase 1) | prefixo |
|---|---|---|---|
| fastapi | **prompt do paper** (`Python_FastAPI/.../FastAPI_Uptime_prompt_Claude.txt`) | voltagent-lang:fastapi-developer | FA |
| flask | **prompt do paper** (`Python_Flask/.../Flask_Uptime_prompt_Claude.txt`) | voltagent-lang:python-pro | FL |
| express | **prompt do paper** (`JavaScript_Express/.../Javascript_Uptime_prompt_Claude.txt`) | voltagent-lang:node-specialist | NS |
| actix | **prompt do paper** (`Rust_Actix/.../Rust_Uptime_prompt_Claude.txt`), com a triagem de `unsafe` | voltagent-lang:rust-engineer | RE |
| django | **novo** (não existe no paper): derivado do fastapi, foco em Django | voltagent-lang:django-developer | DJ |
| aiohttp | **novo** (não existe no paper): derivado do fastapi, foco em aiohttp | voltagent-lang:python-pro | AH |

Única mudança nos templates do paper: a pasta-alvo virou `{target}`, e a linha "Excluded file"
(o `baxbench-prompt.txt`) foi removida, porque o prompt de geração fica fora de `src/` no corpus.
