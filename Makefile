PYTHON ?= .venv/bin/python
SHELL_SCRIPTS = run.sh scripts/setup_python.sh scripts/build_macos_apps.sh \
                scripts/package_release.sh scripts/notarize.sh scripts/release_versions.sh \
                macos/launcher.sh macos/stop.sh

.PHONY: setup setup-test run test lint-sh check app package clean

setup:
	./scripts/setup_python.sh

# The dependency set CI uses; lets a contributor reproduce a CI-only failure.
setup-test:
	$(PYTHON) -m pip install --disable-pip-version-check -r requirements-test.txt

run:
	./run.sh

test:
	mkdir -p .build/test-data .build/pycache
	QWEN_SCRIBE_DATA_DIR="$(CURDIR)/.build/test-data" PYTHONPYCACHEPREFIX="$(CURDIR)/.build/pycache" $(PYTHON) -m unittest discover -s tests -v

# These scripts ship inside the app bundle, so a syntax error is a broken release.
lint-sh:
	bash -n $(SHELL_SCRIPTS)

check: test lint-sh
	$(PYTHON) scripts/check_repo.py
	PYTHONPYCACHEPREFIX="$(CURDIR)/.build/pycache" $(PYTHON) -m compileall -q server.py quantize_8bit.py compare_models.py tests

app:
	./scripts/build_macos_apps.sh

package: app
	./scripts/package_release.sh

clean:
	rm -rf .build dist
