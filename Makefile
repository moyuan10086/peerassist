.PHONY: verify verify-fast test lint frontend docs secrets dependencies dist compose-up compose-down

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

secrets:
	python scripts/verify_repository.py secrets

dependencies:
	python scripts/verify_repository.py dependencies

dist:
	python scripts/verify_repository.py dist

compose-up:
	docker compose up -d

compose-down:
	docker compose down
