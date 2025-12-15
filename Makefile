.PHONY: up down logs migrate createsuperuser

up:
docker-compose up -d --build

down:
docker-compose down

logs:
docker-compose logs -f app worker

migrate:
docker-compose run --rm app alembic upgrade head

createsuperuser:
docker-compose run --rm app python -m app.scripts.bootstrap_admin
