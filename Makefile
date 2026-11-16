.PHONY: test test-keep stack-up stack-down serve sync

# One command: starts Taiga 6 + Postgres in Docker, seeds them, runs the
# integration tests against the real server over MCP/HTTP, tears everything
# down (containers and volumes) even if the tests fail.
test:
	uv sync --frozen
	@trap 'docker compose -f tests/stack/docker-compose.yml down -v --remove-orphans >/dev/null 2>&1' EXIT; \
	  uv run --frozen pytest tests $(ARGS)

# Leave the stack running afterwards (KEEP_STACK=1) for poking around.
test-keep:
	uv sync --frozen
	KEEP_STACK=1 uv run --frozen pytest tests $(ARGS)

stack-down:
	docker compose -f tests/stack/docker-compose.yml down -v --remove-orphans

sync:
	uv sync --frozen

serve:
	uv run --frozen taiga-mcp serve --host 127.0.0.1 --port 8095
