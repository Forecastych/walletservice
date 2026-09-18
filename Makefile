.DEFAULT_GOAL := help
COMPOSE := docker compose

.PHONY: help
help: ## Show this help
	@grep -hE '^[a-zA-Z_-]+:.*?## ' $(MAKEFILE_LIST) \
		| awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-16s\033[0m %s\n", $$1, $$2}'

.PHONY: up
up: ## Build and start the whole stack (database + migrations + API)
	$(COMPOSE) up --build

.PHONY: down
down: ## Stop the stack
	$(COMPOSE) down

.PHONY: clean
clean: ## Stop the stack and delete its data volume
	$(COMPOSE) down --volumes --remove-orphans

.PHONY: logs
logs: ## Follow the API logs
	$(COMPOSE) logs -f api

.PHONY: test
test: ## Run the full test suite in a container
	$(COMPOSE) --profile test run --rm tests

.PHONY: migrate
migrate: ## Apply migrations against the running database
	$(COMPOSE) run --rm migrations

.PHONY: revision
revision: ## Autogenerate a migration: make revision m="add something"
	$(COMPOSE) run --rm migrations alembic revision --autogenerate -m "$(m)"

.PHONY: lint
lint: ## Run ruff and mypy
	ruff check app tests
	ruff format --check app tests
	mypy app

.PHONY: format
format: ## Apply ruff formatting and autofixes
	ruff check --fix app tests
	ruff format app tests

.PHONY: install
install: ## Create a local virtualenv from uv.lock, with dev dependencies
	uv sync --frozen --extra dev

.PHONY: lock
lock: ## Re-resolve dependencies and update uv.lock
	uv lock
