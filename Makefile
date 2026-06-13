SHELL := /bin/sh
COMPOSE := docker compose -f infra/docker-compose.yml --env-file .env
# Prod = base + override merged. Secrets come from .env.prod (materialized
# from your secret store at deploy time — see docs/PRODUCTION.md).
COMPOSE_PROD := docker compose -f infra/docker-compose.yml -f infra/docker-compose.prod.yml --env-file .env.prod

.PHONY: help setup up down logs ps restart pull clean status build kafka-topics prod-config up-prod down-prod

help:
	@echo "Targets:"
	@echo "  setup         First-time clone setup (deps, hooks, .env)"
	@echo "  up            Bring up the whole stack"
	@echo "  down          Stop everything (keep volumes)"
	@echo "  clean         Stop and wipe volumes (DATA LOSS)"
	@echo "  logs          Tail all logs"
	@echo "  ps / status   Show container state"
	@echo "  build         Rebuild service images"
	@echo "  kafka-topics  List Kafka topics"
	@echo "  prod-config   Validate the merged prod compose config"
	@echo "  up-prod       Bring up the stack with the prod overlay"
	@echo "  down-prod     Stop the prod stack (keep volumes)"

setup:
	@bash scripts/setup.sh

up:
	$(COMPOSE) up -d --build

down:
	$(COMPOSE) down

logs:
	$(COMPOSE) logs -f --tail=100

ps status:
	$(COMPOSE) ps

restart:
	$(COMPOSE) restart

pull:
	$(COMPOSE) pull

clean:
	$(COMPOSE) down -v

build:
	$(COMPOSE) build

kafka-topics:
	docker exec -it kafka /opt/kafka/bin/kafka-topics.sh --bootstrap-server localhost:9092 --list

prod-config:
	$(COMPOSE_PROD) config --quiet
	@echo "prod compose config OK"

up-prod:
	$(COMPOSE_PROD) up -d --build

down-prod:
	$(COMPOSE_PROD) down
