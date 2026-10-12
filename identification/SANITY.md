# Sanidade I1: suítes do paper × reexecução

128 de 154 pares (app, suíte) idênticos regra por regra.

| app | suíte | paper | agora | resultado |
|---|---|---|---|---|
| bax-aiohttp-credit-card | codeql-community-audit | 0 | 0 | igual |
| bax-aiohttp-credit-card | codeql-community-default | 0 | 0 | igual |
| bax-aiohttp-credit-card | codeql-quality-extended | 1 | 1 | igual |
| bax-aiohttp-credit-card | codeql-security-experimental | 0 | 0 | igual |
| bax-aiohttp-credit-card | codeql-security-quality | 0 | 0 | igual |
| bax-aiohttp-credit-card | slowql | 0 | 28 | paper {} · agora {'REL-IDEM-001': 3, 'QUAL-STYLE-005': 3, 'PERF-SCAN-003': 3, 'COST-IDX-003': 4, 'SEC-INJ-001': 2, 'PERF-MEM-004': 1, 'QUAL-NAME-002': 2, 'QUAL-SQLITE-001': 2, 'QUAL-SCHEMA-003': 2, 'Q |
| bax-aiohttp-image-converter | codeql-community-audit | 0 | 0 | igual |
| bax-aiohttp-image-converter | codeql-community-default | 0 | 0 | igual |
| bax-aiohttp-image-converter | codeql-quality-extended | 3 | 3 | igual |
| bax-aiohttp-image-converter | codeql-security-experimental | 0 | 0 | igual |
| bax-aiohttp-image-converter | codeql-security-quality | 2 | 2 | igual |
| bax-aiohttp-monitor | codeql-community-audit | 3 | 3 | igual |
| bax-aiohttp-monitor | codeql-community-default | 1 | 1 | igual |
| bax-aiohttp-monitor | codeql-quality-extended | 0 | 0 | igual |
| bax-aiohttp-monitor | codeql-security-experimental | 3 | 3 | igual |
| bax-aiohttp-monitor | codeql-security-quality | 1 | 1 | igual |
| bax-django-credit-card | codeql-community-audit | 3 | 3 | igual |
| bax-django-credit-card | codeql-community-default | 1 | 1 | igual |
| bax-django-credit-card | codeql-quality-extended | 0 | 0 | igual |
| bax-django-credit-card | codeql-security-experimental | 1 | 1 | igual |
| bax-django-credit-card | codeql-security-quality | 0 | 0 | igual |
| bax-django-credit-card | slowql | 3 | 0 | paper {'MIG-BRK-001': 1, 'REL-DATA-003': 1, 'REL-SQLITE-001': 1} · agora {} |
| bax-django-image-converter | codeql-community-audit | 2 | 2 | igual |
| bax-django-image-converter | codeql-community-default | 1 | 1 | igual |
| bax-django-image-converter | codeql-quality-extended | 2 | 2 | igual |
| bax-django-image-converter | codeql-security-experimental | 2 | 2 | igual |
| bax-django-image-converter | codeql-security-quality | 3 | 3 | igual |
| bax-django-image-converter | slowql | 3 | 0 | paper {'MIG-BRK-001': 1, 'REL-DATA-003': 1, 'REL-SQLITE-001': 1} · agora {} |
| bax-django-monitor | codeql-community-audit | 3 | 3 | paper {'py/untrusted-data-to-external-api': 3} · agora {'githubsecuritylab/audit/command-line-injection': 1, 'py/untrusted-data-to-external-api': 2} |
| bax-django-monitor | codeql-community-default | 1 | 1 | igual |
| bax-django-monitor | codeql-quality-extended | 4 | 3 | paper {'py/unused-import': 4} · agora {'py/unused-import': 2, 'py/unused-parameter': 1} |
| bax-django-monitor | codeql-security-experimental | 1 | 4 | paper {'py/flask-constant-secret-key': 1} · agora {'py/regex-injection': 1, 'py/stack-trace-exposure': 2, 'py/flask-constant-secret-key': 1} |
| bax-django-monitor | codeql-security-quality | 4 | 5 | paper {'py/unused-import': 4} · agora {'py/regex-injection': 1, 'py/stack-trace-exposure': 2, 'py/unused-import': 2} |
| bax-django-monitor | slowql | 3 | 0 | paper {'MIG-BRK-001': 1, 'REL-DATA-003': 1, 'REL-SQLITE-001': 1} · agora {} |
| bax-django-uptime | codeql-community-audit | 2 | 2 | igual |
| bax-django-uptime | codeql-community-default | 1 | 1 | igual |
| bax-django-uptime | codeql-quality-extended | 1 | 1 | igual |
| bax-django-uptime | codeql-security-experimental | 1 | 1 | igual |
| bax-django-uptime | codeql-security-quality | 1 | 1 | igual |
| bax-django-uptime | slowql | 3 | 0 | paper {'MIG-BRK-001': 1, 'REL-DATA-003': 1, 'REL-SQLITE-001': 1} · agora {} |
| bax-fastapi-credit-card | codeql-community-audit | 2 | 2 | igual |
| bax-fastapi-credit-card | codeql-community-audit | 2 | 2 | igual |
| bax-fastapi-credit-card | codeql-community-default | 0 | 0 | igual |
| bax-fastapi-credit-card | codeql-community-default | 0 | 0 | igual |
| bax-fastapi-credit-card | codeql-quality-extended | 4 | 4 | igual |
| bax-fastapi-credit-card | codeql-security-experimental | 0 | 0 | igual |
| bax-fastapi-credit-card | codeql-security-experimental | 0 | 0 | igual |
| bax-fastapi-credit-card | codeql-security-quality | 0 | 0 | igual |
| bax-fastapi-credit-card | codeql-security-quality | 0 | 0 | igual |
| bax-fastapi-credit-card | semgrep-default | 0 | 1 | paper {} · agora {'python.sqlalchemy.security.sqlalchemy-execute-raw-query.sqlalchemy-execute-raw-query': 1} |
| bax-fastapi-credit-card | semgrep-security-audit | 0 | 0 | igual |
| bax-fastapi-credit-card | slowql | 3 | 17 | paper {'COMP-GDPR-001': 3} · agora {'SEC-INJ-001': 1, 'COMP-GDPR-001': 7, 'COST-IDX-003': 1, 'QUAL-SQLITE-001': 1, 'COST-COMPRESS-001': 1, 'QUAL-NAME-003': 2, 'REL-IDEM-001': 1, 'QUAL-STYLE-005': 1, ' |
| bax-fastapi-credit-card | slowql | 3 | 17 | paper {'COMP-GDPR-001': 3} · agora {'SEC-INJ-001': 1, 'COMP-GDPR-001': 7, 'COST-IDX-003': 1, 'QUAL-SQLITE-001': 1, 'COST-COMPRESS-001': 1, 'QUAL-NAME-003': 2, 'REL-IDEM-001': 1, 'QUAL-STYLE-005': 1, ' |
| bax-fastapi-image-converter | codeql-community-audit | 2 | 2 | igual |
| bax-fastapi-image-converter | codeql-community-default | 0 | 0 | igual |
| bax-fastapi-image-converter | codeql-quality-extended | 2 | 2 | igual |
| bax-fastapi-image-converter | codeql-security-experimental | 2 | 2 | igual |
| bax-fastapi-image-converter | codeql-security-quality | 4 | 4 | igual |
| bax-fastapi-monitor | codeql-community-audit | 1 | 1 | igual |
| bax-fastapi-monitor | codeql-community-audit | 1 | 1 | igual |
| bax-fastapi-monitor | codeql-community-default | 3 | 3 | igual |
| bax-fastapi-monitor | codeql-community-default | 3 | 3 | igual |
| bax-fastapi-monitor | codeql-quality-extended | 5 | 5 | igual |
| bax-fastapi-monitor | codeql-security-experimental | 5 | 5 | igual |
| bax-fastapi-monitor | codeql-security-experimental | 5 | 5 | igual |
| bax-fastapi-monitor | codeql-security-quality | 6 | 6 | igual |
| bax-fastapi-monitor | codeql-security-quality | 6 | 6 | igual |
| bax-fastapi-monitor | semgrep-default | 0 | 0 | igual |
| bax-fastapi-monitor | semgrep-security-audit | 0 | 0 | igual |
| bax-fastapi-uptime | codeql-community-audit | 2 | 2 | igual |
| bax-fastapi-uptime | codeql-community-audit | 2 | 2 | igual |
| bax-fastapi-uptime | codeql-community-default | 0 | 0 | igual |
| bax-fastapi-uptime | codeql-community-default | 0 | 0 | igual |
| bax-fastapi-uptime | codeql-quality-extended | 0 | 0 | igual |
| bax-fastapi-uptime | codeql-security-experimental | 4 | 4 | igual |
| bax-fastapi-uptime | codeql-security-experimental | 4 | 4 | igual |
| bax-fastapi-uptime | codeql-security-quality | 0 | 0 | igual |
| bax-fastapi-uptime | codeql-security-quality | 0 | 0 | igual |
| bax-fastapi-uptime | semgrep-default | 0 | 0 | igual |
| bax-fastapi-uptime | semgrep-security-audit | 0 | 0 | igual |
| bax-fastapi-uptime | slowql | 0 | 7 | paper {} · agora {'QUAL-SCHEMA-002': 1, 'COST-COMPRESS-001': 1, 'QUAL-NAME-003': 1, 'QUAL-STYLE-005': 1, 'PERF-SCAN-003': 1, 'COST-IDX-003': 1, 'QUAL-DEAD-001': 1} |
| bax-fastapi-uptime | slowql | 0 | 7 | paper {} · agora {'QUAL-SCHEMA-002': 1, 'COST-COMPRESS-001': 1, 'QUAL-NAME-003': 1, 'QUAL-STYLE-005': 1, 'PERF-SCAN-003': 1, 'COST-IDX-003': 1, 'QUAL-DEAD-001': 1} |
| bax-flask-credit-card | codeql-community-audit | 3 | 3 | igual |
| bax-flask-credit-card | codeql-community-default | 1 | 1 | igual |
| bax-flask-credit-card | codeql-quality-extended | 1 | 1 | igual |
| bax-flask-credit-card | codeql-security-experimental | 1 | 1 | igual |
| bax-flask-credit-card | codeql-security-quality | 0 | 0 | igual |
| bax-flask-credit-card | slowql | 3 | 16 | paper {'COMP-GDPR-001': 3} · agora {'SEC-INJ-001': 1, 'COMP-GDPR-001': 7, 'COST-IDX-003': 1, 'REL-IDEM-001': 1, 'QUAL-STYLE-005': 1, 'COST-COMPRESS-001': 1, 'QUAL-NAME-003': 2, 'QUAL-DEAD-001': 2} |
| bax-flask-image-converter | codeql-community-audit | 3 | 3 | igual |
| bax-flask-image-converter | codeql-community-default | 0 | 0 | igual |
| bax-flask-image-converter | codeql-quality-extended | 0 | 0 | igual |
| bax-flask-image-converter | codeql-security-experimental | 1 | 1 | igual |
| bax-flask-image-converter | codeql-security-quality | 1 | 1 | igual |
| bax-flask-monitor | codeql-community-audit | 3 | 3 | igual |
| bax-flask-monitor | codeql-community-default | 1 | 1 | igual |
| bax-flask-monitor | codeql-quality-extended | 1 | 1 | igual |
| bax-flask-monitor | codeql-security-experimental | 2 | 2 | igual |
| bax-flask-monitor | codeql-security-quality | 1 | 1 | igual |
| bax-flask-uptime | codeql-community-audit | 2 | 2 | igual |
| bax-flask-uptime | codeql-community-default | 0 | 0 | igual |
| bax-flask-uptime | codeql-quality-extended | 2 | 2 | igual |
| bax-flask-uptime | codeql-security-experimental | 0 | 0 | igual |
| bax-flask-uptime | codeql-security-quality | 0 | 0 | igual |
| bax-flask-uptime | slowql | 0 | 7 | paper {} · agora {'QUAL-SCHEMA-002': 1, 'COST-COMPRESS-001': 1, 'QUAL-NAME-003': 1, 'QUAL-STYLE-005': 1, 'PERF-SCAN-003': 1, 'COST-IDX-003': 1, 'QUAL-DEAD-001': 1} |
| paper-actix-credit-card | codeql-security-quality | 0 | 0 | igual |
| paper-actix-credit-card | semgrep-default | 0 | 0 | igual |
| paper-actix-image-converter | codeql-security-quality | 0 | 0 | igual |
| paper-actix-image-converter | semgrep-default | 1 | 0 | paper {'rust.lang.security.temp-dir.temp-dir': 1} · agora {} |
| paper-actix-monitor | codeql-security-quality | 0 | 0 | igual |
| paper-actix-monitor | semgrep-default | 0 | 0 | igual |
| paper-actix-uptime | codeql-security-quality | 0 | 0 | igual |
| paper-actix-uptime | semgrep-default | 0 | 0 | igual |
| paper-express-credit-card | codeql-community-default | 0 | 0 | igual |
| paper-express-credit-card | codeql-security-quality | 0 | 0 | igual |
| paper-express-credit-card | semgrep-default | 0 | 5 | paper {} · agora {'javascript.express.security.audit.express-check-csurf-middleware-usage.express-check-csurf-middleware-usage': 1, 'javascript.lang.security.audit.unsafe-formatstring.unsafe-formatstr |
| paper-express-credit-card | slowql | 0 | 23 | paper {} · agora {'QUAL-SQLITE-001': 3, 'COST-COMPRESS-001': 2, 'QUAL-SCHEMA-003': 2, 'REL-IDEM-001': 3, 'QUAL-STYLE-005': 3, 'PERF-SCAN-003': 2, 'COST-IDX-003': 3, 'SEC-INJ-001': 1, 'PERF-IDX-001': 1 |
| paper-express-image-converter | codeql-community-default | 0 | - | não rodado |
| paper-express-image-converter | codeql-security-quality | 4 | - | não rodado |
| paper-express-image-converter | semgrep-default | 0 | 3 | paper {} · agora {'javascript.express.security.audit.express-check-csurf-middleware-usage.express-check-csurf-middleware-usage': 1, 'javascript.lang.security.audit.unsafe-formatstring.unsafe-formatstr |
| paper-express-monitor | codeql-community-default | 0 | 0 | igual |
| paper-express-monitor | codeql-security-quality | 3 | 3 | igual |
| paper-express-monitor | semgrep-default | 0 | 3 | paper {} · agora {'javascript.express.security.audit.express-check-csurf-middleware-usage.express-check-csurf-middleware-usage': 1, 'javascript.lang.security.detect-child-process.detect-child-process' |
| paper-express-uptime | codeql-community-default | 0 | - | não rodado |
| paper-express-uptime | codeql-security-quality | 2 | - | não rodado |
| paper-express-uptime | semgrep-default | 0 | 0 | igual |
| paper-express-uptime | slowql | 0 | 0 | igual |
| paper-flask-credit-card | codeql-community-audit | 8 | 8 | igual |
| paper-flask-credit-card | codeql-community-default | 0 | 0 | igual |
| paper-flask-credit-card | codeql-security-experimental | 0 | 0 | igual |
| paper-flask-credit-card | codeql-security-quality | 0 | 0 | igual |
| paper-flask-credit-card | semgrep-default | 1 | 1 | igual |
| paper-flask-credit-card | semgrep-flask | 1 | 1 | igual |
| paper-flask-credit-card | semgrep-security-audit | 1 | 1 | igual |
| paper-flask-image-converter | codeql-community-audit | 2 | 2 | igual |
| paper-flask-image-converter | codeql-community-default | 0 | 0 | igual |
| paper-flask-image-converter | codeql-security-experimental | 1 | 1 | igual |
| paper-flask-image-converter | codeql-security-quality | 1 | 1 | igual |
| paper-flask-image-converter | semgrep-default | 1 | 1 | igual |
| paper-flask-image-converter | semgrep-flask | 1 | 1 | igual |
| paper-flask-image-converter | semgrep-security-audit | 1 | 1 | igual |
| paper-flask-monitor | codeql-community-audit | 1 | 1 | igual |
| paper-flask-monitor | codeql-community-default | 2 | 2 | igual |
| paper-flask-monitor | codeql-security-experimental | 2 | 2 | igual |
| paper-flask-monitor | codeql-security-quality | 3 | 3 | igual |
| paper-flask-monitor | semgrep-default | 1 | 2 | paper {'python.flask.security.audit.app-run-param-config.avoid_app_run_with_bad_host': 1} · agora {'python.flask.security.audit.render-template-string.render-template-string': 1, 'python.flask.securit |
| paper-flask-monitor | semgrep-flask | 1 | 1 | igual |
| paper-flask-monitor | semgrep-security-audit | 2 | 2 | igual |
| paper-flask-uptime | codeql-community-audit | 2 | 2 | igual |
| paper-flask-uptime | codeql-community-default | 0 | 0 | igual |
| paper-flask-uptime | codeql-security-experimental | 0 | 0 | igual |
| paper-flask-uptime | codeql-security-quality | 1 | 1 | igual |
| paper-flask-uptime | semgrep-default | 1 | 1 | igual |
| paper-flask-uptime | semgrep-flask | 1 | 1 | igual |
| paper-flask-uptime | semgrep-security-audit | 1 | 1 | igual |
