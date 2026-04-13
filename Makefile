.PHONY: setup sync ingest compile lint check test format type-check serve wiki wiki-build snapshot snapshot-list snapshot-clean bootstrap publish help

help: ## Show this help
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | sort | \
		awk 'BEGIN {FS = ":.*?## "}; {printf "\033[36m%-20s\033[0m %s\n", $$1, $$2}'

setup: ## First-time setup: install uv, sync deps
	@command -v uv >/dev/null 2>&1 || curl -LsSf https://astral.sh/uv/install.sh | sh
	uv sync
	cp -n .env.example .env 2>/dev/null || true
	@echo "Setup complete. Edit .env with your API keys."

sync: ## Sync dependencies
	uv sync

# === Email Operations ===

ingest: ## Ingest last 30 days of mailing list email
	uv run python scripts/ingest_backlog.py --days 30

ingest-all: ## Ingest all available email (no date filter)
	uv run python scripts/ingest_backlog.py

compile: ## Compile all unprocessed raw emails into wiki
	uv run python scripts/compile_all.py

lint-wiki: ## Run wiki health checks
	uv run python scripts/lint_wiki.py

lint-wiki-fix: ## Run wiki health checks and auto-fix safe issues
	uv run python scripts/lint_wiki.py --fix

# === Full Pipeline ===

pipeline: ingest compile lint-wiki ## Run full pipeline: ingest → compile → lint

# === Code Quality ===

check: format-check lint type-check test ## Run all quality checks

test: ## Run tests
	uv run pytest tests/ -x --tb=short

lint: ## Lint code
	uv run ruff check src/ scripts/ tests/

format: ## Format code
	uv run ruff format src/ scripts/ tests/

format-check: ## Check formatting without fixing
	uv run ruff format --check src/ scripts/ tests/

type-check: ## Type check
	uv run mypy src/

# === Snapshot / Restore (safe experimentation) ===

snapshot: ## Snapshot current wiki/ into .snapshots/ with timestamp label
	uv run python scripts/snapshot_wiki.py save

snapshot-list: ## List available snapshots
	uv run python scripts/snapshot_wiki.py list

snapshot-clean: ## Delete all wiki/ .md content (keeps structure). Needs --confirm
	uv run python scripts/snapshot_wiki.py clean --confirm

# === Wiki Browsing ===

wiki: ## Serve wiki on localhost only (http://127.0.0.1:8765)
	uv run mkdocs serve --dev-addr 127.0.0.1:8765

wiki-lan: ## Serve wiki on LAN (phone-accessible). Shows IP to use.
	@echo "Serving on LAN. On phone (same WiFi), visit:"
	@ip=$$(ipconfig getifaddr en0 2>/dev/null || ipconfig getifaddr en1 2>/dev/null); \
		echo "  http://$$ip:8765/"
	@echo "macOS firewall may prompt — accept the incoming connection."
	uv run mkdocs serve --dev-addr 0.0.0.0:8765

wiki-build: ## Build static wiki site into ./site/ for deployment
	uv run mkdocs build

serve: wiki ## Alias for `make wiki`

# === GCP Deployment (see docs/gcp-migration.md) ===

bootstrap: ## One-time GCP bootstrap: create bucket + enable APIs (idempotent)
	bash scripts/gcp/bootstrap.sh

publish: ## Sync raw/wiki to GCS + redeploy Cloud Run viewer (Cloud Build rebuilds the site)
	gsutil -m rsync -r raw/  gs://indiamart-email-kb/raw/
	gsutil -m rsync -r wiki/ gs://indiamart-email-kb/wiki/
	bash scripts/gcp/deploy-viewer.sh
