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
.PHONY: help up build-up down logs ps owner preflight doctor backup restore update test

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

doctor: ## Report what the running deployment can actually do
	$(COMPOSE) exec api six-community doctor

backup: ## Write an authenticated backup to SIX_BACKUP_DIR
	bash deploy/community/backup.sh $(ENV_FILE)

# restore.sh refuses to run without --confirm RESTORE. That guard is the point,
# so this target asks for the same word rather than supplying it: a shortcut
# must not make a destructive operation easier to reach than the script it wraps.
restore: ## Replace all data from a backup: make restore BACKUP=/abs/dir CONFIRM=RESTORE
	@test -n "$(BACKUP)" || { \
		echo "set BACKUP to the absolute backup directory, for example:" >&2; \
		echo "  make restore BACKUP=/srv/six/backups/2026-09-14 CONFIRM=RESTORE" >&2; \
		exit 2; }
	@test "$(CONFIRM)" = "RESTORE" || { \
		echo "This replaces the database and every uploaded document with the backup." >&2; \
		echo "Repeat with CONFIRM=RESTORE once you are sure." >&2; \
		exit 2; }
	SIX_SELFHOST_ENV_FILE=$(ENV_FILE) bash deploy/community/restore.sh \
		--backup "$(BACKUP)" --confirm RESTORE

update: ## Apply a newer checkout or newer published images
	bash deploy/community/preflight.sh $(ENV_FILE)
	$(COMPOSE) build
	$(COMPOSE) up --detach --wait

test: ## Run the deployment script and boundary tests
	bash deploy/community/test.sh
