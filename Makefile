UV ?= uv

.PHONY: sync check format lint typecheck test
sync:
	$(UV) sync --locked
check: format lint typecheck test
format:
	$(UV) run --locked ruff format --check .
lint:
	$(UV) run --locked ruff check .
typecheck:
	$(UV) run --locked mypy
test:
	$(UV) run --locked pytest --cov=ai_trading_lab --cov-report=term-missing
