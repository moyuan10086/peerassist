.PHONY: verify verify-fast test lint frontend docs design secrets dependencies dist compose-up compose-down m1-smoke m1-system platform m1-all m1-compose-config

verify:
	python scripts/verify_repository.py all

verify-fast:
	python scripts/verify_repository.py fast

test:
	python scripts/verify_repository.py python

lint:
	python scripts/verify_repository.py lint

frontend:
	python scripts/verify_repository.py frontend

docs:
	python scripts/verify_repository.py docs

design:
	python scripts/check_design_md.py

secrets:
	python scripts/verify_repository.py secrets

dependencies:
	python scripts/verify_repository.py dependencies

dist:
	python scripts/verify_repository.py dist

compose-up:
	docker compose -f infrastructure/compose/compose.yml up --build

compose-down:
	docker compose -f infrastructure/compose/compose.yml down

m1-smoke:
	bash scripts/m1_compose_smoke.sh

platform:
	python scripts/verify_repository.py platform

m1-system:
	python scripts/verify_repository.py m1-system

m1-all:
	python scripts/verify_repository.py m1-all

m1-compose-config:
	docker compose -f infrastructure/compose/compose.m1.yml config --quiet
