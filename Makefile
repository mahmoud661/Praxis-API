SHELL := /bin/sh
COMPOSE := docker compose -f infra/docker-compose.yml --env-file .env

.PHONY: help setup up down logs ps restart pull clean status build kafka-topics

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
