eval: ## run the eval suites (docs/lld/08-eval.md); ARGS="--suite safety --trials 3"
	uv run agent eval $(ARGS)

test:
	uv run pytest

check:
	uv run ruff check . && uv run pyright && uv run pytest

.PHONY: eval test check
