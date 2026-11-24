# Запуск практических работ № 6–9 с нуля

Нужны **Python 3.11 или 3.12**, **Node.js 22 или новее с npm 10+** (для ЛР № 8), **Docker с Compose v2** и браузер. На Windows запустите Docker Desktop в режиме Linux containers. Скопируйте весь проект, включая `sirius-food-frontend/package-lock.json` и `observability/`. Старое виртуальное окружение и папку `node_modules` переносить не нужно. Для первой установки нужен интернет.

## 1. Установка и подготовка базы

Команды запускайте из корня проекта. Для Python 3.12 замените `3.11` в первой команде на `3.12`. Если на Windows команда `py` не найдена, проверьте версию через `python --version` и создайте окружение командой `python -m venv .venv`. Если локальный `.env` уже есть, пропустите копирование и сверьте параметры с `.env.example`.

**Windows, PowerShell:**

```powershell
py -3.11 -m venv .venv
Copy-Item .env.example .env
$py = ".\.venv\Scripts\python.exe"
& $py -m pip install -r requirements-dev.txt
docker compose up -d --wait db redis
& $py -m alembic upgrade head
& $py -m scripts.seed_demo
```

**Linux / macOS:**

```bash
python3.11 -m venv .venv
cp .env.example .env
. .venv/bin/activate
python -m pip install -r requirements-dev.txt
docker compose up -d --wait db redis
python -m alembic upgrade head
python -m scripts.seed_demo
```

В следующих командах `python` — интерпретатор из `.venv`. На Windows в **каждом новом окне** из корня проекта задайте `$py = ".\.venv\Scripts\python.exe"` и замените начало команды `python` на `& $py`. В Linux/macOS в каждом окне выполните `. .venv/bin/activate`.

Для нового запуска используйте пустую базу. Если прежняя база уже под Alembic, выполните `python -m alembic upgrade head`. Если таблицы созданы старой версией через `create_all` без таблицы `alembic_version`, безопаснее поднять отдельную базу для защиты (другое имя проекта Compose и свободный `DB_PORT`, затем тот же порт в `DATABASE_URL`). Не удаляйте старый volume.

Локальные аккаунты после seed: `student@sirius.ru`, `cook@sirius.ru`, `courier@sirius.ru`, `admin@sirius.ru`. Пароль у всех `Passw0rd!`. Seed добавляет тестовых пользователей только при их отсутствии, создаёт шесть блюд и не сбрасывает баланс.

## 2. Общий API и ЛР № 6

В отдельном окне:

```text
python -m uvicorn app.main:app --host 0.0.0.0 --port 8000 --no-access-log
```

Проверьте [health](http://127.0.0.1:8000/health) (оба поля `ok`), [меню](http://127.0.0.1:8000/menu/items) и [Swagger](http://127.0.0.1:8000/docs). `DEBUG=True` из примера включает Swagger. Для ЛР № 6 запустите в другом окне:

```text
python delivery_server.py
```

В третьем окне:

```text
python -m scripts.seed_lab6
python scripts/demo_lab6.py
```

Детали стримов, чата и проверки остановленного gRPC-сервера — [защита № 6](lab6_defense.md).

## 3. ЛР № 7: брокер и четыре фоновых процесса

```text
docker compose up -d --wait rabbitmq
python -m app.workers.dlq_setup
```

Оставьте API и gRPC запущенными. Каждая строка ниже запускается в **отдельном окне** из корня проекта; дождитесь сообщений `consumer_ready` и `outbox_ready`.

```text
python -m app.workers.kitchen_consumer
python delivery_worker.py
python -m app.workers.payment_consumer
python -m app.workers.outbox
```

Демонстрация через `python -m scripts.demo_orders`; состояние заказа — `python -m scripts.demo_orders --show НОМЕР` с числовым ID из ответа. RabbitMQ Management: [15672](http://127.0.0.1:15672), `sirius / sirius`. Сценарии повторов, аварии брокера и компенсации — [защита № 7](lab7_defense.md).

## 4. ЛР № 8: витрина

Для витрины достаточно API, PostgreSQL, Redis и заполненного меню. Рабочее окно фронтенда:

```powershell
Set-Location sirius-food-frontend
Copy-Item .env.example .env.local
npm.cmd ci
npm.cmd run dev
```

В Linux/macOS: `cd sirius-food-frontend`, `cp .env.example .env.local`, `npm ci`, `npm run dev`. Откройте [витрину](http://localhost:3000). Для production-проверки остановите dev и выполните `npm run build` и `npm start` (`npm.cmd` в PowerShell). [Защита № 8](lab8_defense.md).

## 5. ЛР № 9: Prometheus, Grafana и логи

Поднимите процессы ЛР № 7, затем:

```text
docker compose up -d prometheus grafana
```

В [Prometheus Targets](http://127.0.0.1:9090/targets) через несколько секунд `sirius_app` и `rabbitmq` должны стать **UP**. Откройте [дашборд Grafana](http://localhost:3001/d/sirius-lab9), вход `admin / admin`; при первом входе Grafana может предложить сменить пароль. Порт **3001** оставляет **3000** витрине. Источник данных уже настроен.

Для расследования сохраняйте stdout API и кухни в разные файлы. Например, добавьте к командам из разделов 2–3 в PowerShell `| Tee-Object -FilePath api.log` и `| Tee-Object -FilePath kitchen.log`, в bash — `| tee api.log` и `| tee kitchen.log`. Перезапустите соответствующие процессы с таким перенаправлением до отправки заказа. [Защита № 9](lab9_defense.md) содержит команды поиска и нагрузку для проверки панелей.

## Проверка и остановка

- `python -m pytest -q` из корня: нужны PostgreSQL и Redis. Используйте **учебную** базу: интеграционные тесты пишут в неё. Запускайте тесты отдельно от демонстрации.
- Если заняты порты PostgreSQL, Redis или RabbitMQ: поменяйте `DB_PORT` / `REDIS_PORT` / `RABBITMQ_PORT` в `.env` и соответствующий URL подключения там же.
- Если API работает на другом порту, обновите `API_URL` в `sirius-food-frontend/.env.local` и таргет `sirius_app` в `observability/prometheus.yml`. Для недоступного таргета проверьте, что API слушает `0.0.0.0`, а локальный экран разрешает запросы из сети Docker на порт API.
- Остановка: `Ctrl+C` в окнах процессов, затем `docker compose down` из корня. Именованные volumes останутся, поэтому меню и результаты сохранятся.
