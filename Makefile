.PHONY: up down build migrate makemigrations shell test lint fmt logs

up:
	docker compose up -d

down:
	docker compose down

build:
	docker compose build

migrate:
	docker compose exec web python manage.py migrate

makemigrations:
	docker compose exec web python manage.py makemigrations

shell:
	docker compose exec web python manage.py shell

test:
	pytest --cov=apps

lint:
	ruff check . && black --check . && isort --check-only .

fmt:
	ruff check . --fix && black . && isort .

logs:
	docker compose logs -f
