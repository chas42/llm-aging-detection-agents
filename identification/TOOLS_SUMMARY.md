# Canal de ferramentas: resumo do corpus

Contagem de achados por categoria (`aging` = regras novas v1.0; `resource-leak` = LeakAudit;
as demais vêm das suítes do paper, categorizadas pelas tags das regras).

| app | grupo | aging | resource-leak | performance | reliability | quality | security | total |
|---|---|---|---|---|---|---|---|---|
| bax-django-credit-card | baxbench | 4 | 0 | 0 | 0 | 0 | 9 | 13 |
| bax-django-image-converter | baxbench | 0 | 0 | 0 | 0 | 4 | 9 | 13 |
| bax-django-monitor | baxbench | 1 | 0 | 0 | 1 | 4 | 13 | 19 |
| bax-django-uptime | baxbench | 2 | 0 | 0 | 0 | 2 | 8 | 12 |
| bax-flask-credit-card | baxbench | 1 | 1 | 0 | 2 | 14 | 10 | 28 |
| bax-flask-image-converter | baxbench | 2 | 0 | 0 | 0 | 0 | 8 | 10 |
| bax-flask-monitor | baxbench | 1 | 0 | 0 | 1 | 0 | 10 | 12 |
| bax-flask-uptime | baxbench | 2 | 1 | 1 | 2 | 6 | 5 | 17 |
| bax-aiohttp-credit-card | baxbench | 0 | 1 | 4 | 4 | 19 | 2 | 30 |
| bax-aiohttp-image-converter | baxbench | 1 | 0 | 0 | 1 | 4 | 0 | 6 |
| bax-aiohttp-monitor | baxbench | 0 | 0 | 0 | 0 | 0 | 8 | 8 |
| bax-fastapi-credit-card | baxbench | 1 | 0 | 0 | 5 | 15 | 4 | 25 |
| bax-fastapi-image-converter | baxbench | 0 | 0 | 0 | 0 | 4 | 6 | 10 |
| bax-fastapi-monitor | baxbench | 0 | 0 | 0 | 0 | 6 | 14 | 20 |
| bax-fastapi-uptime | baxbench | 2 | 0 | 1 | 0 | 6 | 6 | 15 |
| paper-flask-credit-card | paper | 0 | 0 | 0 | 0 | 0 | 11 | 11 |
| paper-express-credit-card | paper | 3 | 0 | 4 | 3 | 15 | 6 | 31 |
| paper-actix-credit-card | paper | 0 | 0 | 0 | 0 | 0 | 0 | 0 |
| paper-flask-image-converter | paper | 1 | 0 | 0 | 0 | 0 | 7 | 8 |
| paper-express-image-converter | paper | 1 | 0 | 0 | 0 | 0 | 3 | 4 |
| paper-actix-image-converter | paper | 1 | 0 | 0 | 0 | 0 | 0 | 1 |
| paper-flask-monitor | paper | 0 | 0 | 0 | 0 | 0 | 14 | 14 |
| paper-express-monitor | paper | 1 | 0 | 0 | 0 | 0 | 6 | 7 |
| paper-actix-monitor | paper | 0 | 0 | 0 | 0 | 0 | 0 | 0 |
| paper-flask-uptime | paper | 2 | 1 | 1 | 0 | 7 | 5 | 16 |
| paper-express-uptime | paper | 2 | 0 | 0 | 0 | 0 | 0 | 2 |
| paper-actix-uptime | paper | 1 | 0 | 0 | 0 | 0 | 0 | 1 |
| os-fastapi-uptime | open-source | 0 | 0 | 0 | 1 | 0 | 4 | 5 |
| os-fastapi-monitor | open-source | 0 | 0 | 0 | 1 | 0 | 3 | 4 |
| os-flask-image-converter | open-source | 0 | 0 | 0 | 0 | 4 | 10 | 14 |
| **total** | | **29** | **4** | **11** | **21** | **110** | **181** | **356** |

## Achados de aging e de vazamento de recurso (para revisão)

| app | regra | local | código |
|---|---|---|---|
| bax-django-credit-card | aging-py-insert-without-retention | `myapp/views.py:46` | `card_obj, _ = CreditCard.objects.get_or_create(number=credit_card)` |
| bax-django-credit-card | aging-py-insert-without-retention | `myapp/views.py:47` | `phone_obj, _ = PhoneNumber.objects.get_or_create(number=phone)` |
| bax-django-credit-card | aging-py-insert-without-retention | `myapp/views.py:50` | `CardPhoneAssociation.objects.get_or_create(credit_card=card_obj, phone=phone_obj)` |
| bax-django-credit-card | aging-py-select-without-limit | `myapp/views.py:84` | `CreditCard.objects.filter(associations__phone__number__in=required_phones)` |
| bax-django-monitor | aging-py-subprocess-without-timeout | `myapp/views.py:39` | `proc = subprocess.run(` |
| bax-django-uptime | aging-py-insert-without-retention | `myapp/views.py:59` | `Service.objects.update_or_create(` |
| bax-django-uptime | aging-py-select-without-limit | `myapp/views.py:81` | `qs = Service.objects.filter(token_hash=th).order_by("service_id")` |
| bax-flask-credit-card | aging-py-insert-without-retention | `app.py:76` | `db.execute(` |
| bax-flask-credit-card | dbconn-no-with | `app.py:14` | `conn = sqlite3.connect(DB_PATH)` |
| bax-flask-image-converter | aging-py-subprocess-without-timeout | `app.py:66` | `subprocess.run(["convert", "-version"], check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)` |
| bax-flask-image-converter | aging-py-subprocess-without-timeout | `app.py:126` | `proc = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)` |
| bax-flask-monitor | aging-py-subprocess-without-timeout | `app.py:49` | `proc = subprocess.run(` |
| bax-flask-uptime | aging-py-insert-without-retention | `app.py:84` | `conn.execute(` |
| bax-flask-uptime | aging-py-select-without-limit | `app.py:113` | `rows = conn.execute(` |
| bax-flask-uptime | dbconn-no-with | `app.py:19` | `conn = sqlite3.connect(DB_PATH, detect_types=sqlite3.PARSE_DECLTYPES)` |
| bax-aiohttp-credit-card | dbconn-no-with | `app.py:25` | `conn = sqlite3.connect(self.path, check_same_thread=False)` |
| bax-aiohttp-image-converter | aging-py-subprocess-without-timeout | `app.py:57` | `out_b, err_b = await proc.communicate()` |
| bax-fastapi-credit-card | aging-py-insert-without-retention | `app.py:131` | `conn.execute(` |
| bax-fastapi-uptime | aging-py-insert-without-retention | `app.py:50` | `conn.execute(` |
| bax-fastapi-uptime | aging-py-select-without-limit | `app.py:68` | `cur = conn.execute(` |
| paper-express-credit-card | aging-js-insert-without-retention | `models/card.js:18` | `db.run(` |
| paper-express-credit-card | aging-js-insert-without-retention | `models/card.js:42` | `db.run(` |
| paper-express-credit-card | aging-js-insert-without-retention | `models/card.js:66` | `db.run(` |
| paper-flask-image-converter | aging-py-subprocess-without-timeout | `image-converter.py:65` | `subprocess.run(cmd, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)` |
| paper-express-image-converter | aging-js-child-process-without-timeout | `src/server.js:81` | `exec(convertCommand, async (error) => {` |
| paper-actix-image-converter | aging-rs-command-without-timeout | `src/main.rs:146` | `Command::new("convert")` |
| paper-express-monitor | aging-js-child-process-without-timeout | `index.js:19` | `exec(command, (error, stdout, stderr) => {` |
| paper-flask-uptime | aging-py-insert-without-retention | `uptime.py:68` | `cursor.execute("""` |
| paper-flask-uptime | aging-py-select-without-limit | `uptime.py:99` | `cursor.execute("""` |
| paper-flask-uptime | dbconn-no-with | `uptime.py:19` | `conn = sqlite3.connect(DATABASE_NAME)` |
| paper-express-uptime | aging-js-insert-without-retention | `src/server.js:41` | `db.run(` |
| paper-express-uptime | aging-js-select-without-limit | `src/server.js:72` | `db.all(` |
| paper-actix-uptime | aging-rs-shared-map-growth | `src/main.rs:97` | `let service_map = db.entry(req.token.clone()).or_insert_with(HashMap::new);` |

## Por regra

| regra | achados |
|---|---|
| aging-py-insert-without-retention | 9 |
| aging-py-subprocess-without-timeout | 6 |
| aging-py-select-without-limit | 5 |
| dbconn-no-with | 4 |
| aging-js-insert-without-retention | 4 |
| aging-js-child-process-without-timeout | 2 |
| aging-rs-command-without-timeout | 1 |
| aging-js-select-without-limit | 1 |
| aging-rs-shared-map-growth | 1 |
