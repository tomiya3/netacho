.PHONY: setup test lint viewer
setup:
	scripts/setup.sh
test:
	uv run pytest -q
lint:
	uv run ruff check .
viewer:  ## 視聴画面。http://localhost:8000/viewer/?run=../out/view
	uv run python -m http.server -d . 8000
