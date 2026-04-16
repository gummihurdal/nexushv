.PHONY: help install dev test test-api test-ha test-ai lint build run stop status clean

help: ## Show this help
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | sort | awk 'BEGIN {FS = ":.*?## "}; {printf "\033[36m%-20s\033[0m %s\n", $$1, $$2}'

install: ## Install all dependencies
	python3 -m venv venv
	. venv/bin/activate && pip install -r requirements.txt
	. venv/bin/activate && pip install pytest httpx
	cd ui && npm install

dev: ## Start development servers (API + HA + UI dev)
	@echo "Starting NexusHV development environment..."
	. venv/bin/activate && python3 api/nexushv_api.py &
	. venv/bin/activate && python3 ha/nexushv_ha.py --standalone --port 8081 &
	cd ui && npm run dev &
	@echo "API: http://localhost:8080"
	@echo "HA:  http://localhost:8081"
	@echo "UI:  http://localhost:3000"
	@echo "Docs: http://localhost:8080/api/docs"

run: ## Start production services
	./scripts/nexushv-supervisor.sh start

stop: ## Stop all services
	./scripts/nexushv-supervisor.sh stop

status: ## Check service status
	./scripts/nexushv-supervisor.sh status

test: ## Run all tests
	. venv/bin/activate && python3 -m pytest tests/ -v --tb=short

test-api: ## Run API tests only
	. venv/bin/activate && python3 -m pytest tests/test_api.py -v --tb=short

test-ha: ## Run HA tests only
	. venv/bin/activate && python3 -m pytest tests/test_ha.py -v --tb=short

test-ai: ## Run AI tests only
	. venv/bin/activate && python3 -m pytest tests/test_ai.py -v --tb=short

test-load: ## Run load test (50 requests, 5 concurrent)
	. venv/bin/activate && python3 tests/test_load.py 50 5

lint: ## Check Python syntax
	python3 -m py_compile api/nexushv_api.py
	python3 -m py_compile ha/nexushv_ha.py
	python3 -m py_compile ai/nexushv_ai_local.py
	@echo "All files compile successfully"

build: ## Build frontend for production
	cd ui && npm run build

clean: ## Clean build artifacts and caches
	rm -rf __pycache__ api/__pycache__ ha/__pycache__ ai/__pycache__
	rm -rf tests/__pycache__ .pytest_cache
	rm -rf ui/dist ui/node_modules/.cache

docker: ## Build and start with Docker Compose
	docker compose up --build -d

docker-stop: ## Stop Docker containers
	docker compose down
