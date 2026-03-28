.PHONY: install test test-attacks test-scale benchmark-scale lint run demo docker-up docker-down clean cache-tiles help

help:
	@echo "Available targets:"
	@echo "  install      - Install dependencies from requirements.txt"
	@echo "  test         - Run all tests with verbose coverage"
	@echo "  test-attacks - Run attack tests with -s flag"
	@echo "  test-scale   - Run scale tests"
	@echo "  benchmark-scale - Run F-10 scale benchmark (50 nodes)"
	@echo "  lint         - Run flake8 with max line length 100"
	@echo "  run          - Start server and simulation with 10 nodes"
	@echo "  demo         - Run the demo script"
	@echo "  docker-up    - Start the docker-compose stack"
	@echo "  docker-down  - Stop the docker-compose stack"
	@echo "  clean        - Remove pycache and pytest cache directories"
	@echo "  cache-tiles  - Run the OSM tile caching script"

install:
	pip install -r requirements.txt

test:
	pytest tests/ -v --cov=backend --cov=simulation

test-attacks:
	pytest tests/test_attacks.py -s -v

test-scale:
	pytest tests/test_scale.py -s -v

benchmark-scale:
	python scripts/scale_benchmark.py --nodes 50 --output logs/f10_scale_report.json

lint:
	flake8 . --max-line-length=100

run:
	python run_simulation.py --nodes 10

demo:
	bash scripts/demo.sh

docker-up:
	docker compose up -d

docker-down:
	docker compose down

clean:
	find . -type d -name "__pycache__" -exec rm -rf {} +
	find . -type d -name ".pytest_cache" -exec rm -rf {} +
	rm -f .coverage

cache-tiles:
	python scripts/cache_osm_tiles.py
