.PHONY: setup test lint viewer
setup:
	scripts/setup.sh
test:
	uv run pytest -q
lint:
	uv run ruff check .
PORT ?= 8000
viewer:  ## 視聴画面。http://localhost:$(PORT)/viewer/?run=../out/view（8000 が塞がっていれば make viewer PORT=8765）
	uv run python -m http.server -d . $(PORT)
