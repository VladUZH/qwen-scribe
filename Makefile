PYTHON ?= .venv/bin/python

.PHONY: setup run test check app package clean

setup:
	./scripts/setup_python.sh

run:
	./run.sh

test:
	mkdir -p .build/test-data .build/pycache
	QWEN_SCRIBE_DATA_DIR="$(CURDIR)/.build/test-data" PYTHONPYCACHEPREFIX="$(CURDIR)/.build/pycache" $(PYTHON) -m unittest discover -s tests -v

check: test
	$(PYTHON) scripts/check_repo.py
	PYTHONPYCACHEPREFIX="$(CURDIR)/.build/pycache" $(PYTHON) -m compileall -q server.py quantize_8bit.py compare_models.py tests

app:
	./scripts/build_macos_apps.sh

package: app
	./scripts/package_release.sh

clean:
	rm -rf .build dist
