# YouTube Scout

Веб-додаток для аналізу YouTube та генерації тем із AI. Стек: FastAPI, PostgreSQL, Redis/RQ, HTMX.

## Запуск локально (Docker)
1. Скопіюйте `.env.example` в `.env` та заповніть секрети.
2. Запустіть
```bash
make up
```
3. Застосувати міграції:
```bash
make migrate
```
4. Bootstrap адміністратора (якщо ще не створений):
```bash
make createsuperuser
```
5. Відкрийте http://localhost:8000

Зупинити сервіси: `make down`

## Ручний запуск без Docker
```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
alembic upgrade head
uvicorn app.main:app --reload
```

## Основне
- Авторизація через email/пароль, ролі `admin` та `user`.
- Довгі задачі (аналіз YouTube, генерація тем) запускаються як RQ jobs, статус доступний через `/api/jobs/{id}`.
- Сторінки: `/dashboard` (аналіз), `/topics`, `/keywords`, `/movers`, `/admin/users`.
- Тексти інтерфейсу українською. AI контент англійською за вимогою.
