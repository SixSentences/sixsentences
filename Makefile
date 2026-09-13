# Self-hosting shortcuts. Every target is a thin wrapper around the documented
# command it runs, so nothing here is required to operate a deployment.
#
# Override the environment file for a second deployment:
#   make up ENV_FILE=/srv/six/production.env

ENV_FILE ?= .env.selfhost
COMPOSE := docker compose --env-file $(ENV_FILE)
EMAIL ?=
ORG ?= My Lab
FIRST_NAME ?=

.DEFAULT_GOAL := help
.PHONY: help up build-up down logs ps owner preflight backup update test

help: ## Show the available targets
	@awk 'BEGIN { FS = ":.*## " } /^[a-z-]+:.*## / { printf "  make %-12s %s\n", $$1, $$2 }' $(MAKEFILE_LIST)
	@echo
	@echo "  ENV_FILE=$(ENV_FILE)"

up: ## Configure, check, start, and create the first owner (local HTTP)
	bash deploy/community/quickstart.sh --env-file $(ENV_FILE)

build-up: ## Same as up, but always build the images from this checkout
	bash deploy/community/quickstart.sh --env-file $(ENV_FILE) --build

down: ## Stop the stack and keep the volumes
	$(COMPOSE) down

logs: ## Follow the application logs
	$(COMPOSE) logs --tail 100 --follow api worker web proxy

ps: ## Show container status
	$(COMPOSE) ps

owner: ## Create one tenant owner: make owner EMAIL=you@example.org
	@test -n "$(EMAIL)" || { echo "set EMAIL, for example: make owner EMAIL=you@example.org" >&2; exit 2; }
	$(COMPOSE) run --rm api six-community auth create-owner \
		--email "$(EMAIL)" --org "$(ORG)" --first-name "$(FIRST_NAME)"

preflight: ## Re-run the fail-closed configuration check
	bash deploy/community/preflight.sh $(ENV_FILE)

backup: ## Write an authenticated backup to SIX_BACKUP_DIR
	bash deploy/community/backup.sh $(ENV_FILE)

update: ## Apply a newer checkout or newer published images
	bash deploy/community/preflight.sh $(ENV_FILE)
	$(COMPOSE) build
	$(COMPOSE) up --detach --wait

test: ## Run the deployment script and boundary tests
	bash deploy/community/test.sh
