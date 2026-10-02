PYTHON ?= python3
CONFIG ?=
RAW ?=
ENV_FILE ?= .env

.PHONY: demo validate test docs fetch-list refresh

## demo: offline demo, no account or key needed
demo:
	$(PYTHON) demo.py

## validate: workflow checks plus docs freshness (CONFIG=... adds your blocked terms)
validate:
	$(PYTHON) tools/validate.py $(if $(CONFIG),--config $(CONFIG))
	$(PYTHON) tools/build_docs.py --check

## test: pytest suite
test:
	$(PYTHON) -m pytest -q

## docs: regenerate the READMEs from catalog.toml and the JSON
docs:
	$(PYTHON) tools/build_docs.py

## fetch-list: list the workflows on your instance (needs .env)
fetch-list:
	$(PYTHON) tools/fetch.py --env-file $(ENV_FILE) --list

## refresh: export, sanitize, document and validate (needs CONFIG, RAW and an .env; ENV_FILE=... to use another one)
refresh:
	@test -n "$(CONFIG)" -a -n "$(RAW)" || { echo "usage: make refresh CONFIG=/path/outside/repo/config.json RAW=/tmp/n8n-raw"; exit 2; }
	$(PYTHON) tools/fetch.py --env-file $(ENV_FILE) --out $(RAW) --manifest $(CONFIG)
	$(PYTHON) tools/sanitize.py --config $(CONFIG) --raw $(RAW) --env-file $(ENV_FILE)
	$(PYTHON) tools/build_docs.py
	$(PYTHON) tools/validate.py --config $(CONFIG)
