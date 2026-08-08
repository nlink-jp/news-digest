SKILL   := news-digest
VERSION ?= $(shell git describe --tags --always --dirty 2>/dev/null || echo dev)
DEST    ?= $(HOME)/.claude/skills

.PHONY: install uninstall check test package clean

# Install what a user installs. Building the zip and unpacking *that* means a
# packaging defect breaks this install instead of reaching a release unnoticed
# — copying the source tree exercises a path no user takes. The removal
# matters too: copying over a previous install leaves behind files that have
# since been renamed or deleted.
install: package
	@rm -rf "$(DEST)/$(SKILL)"
	@mkdir -p "$(DEST)"
	@unzip -q "dist/$(SKILL)-$(VERSION).zip" -d "$(DEST)"
	@echo "installed from dist/$(SKILL)-$(VERSION).zip -> $(DEST)/$(SKILL)"

uninstall:
	@rm -rf "$(DEST)/$(SKILL)"
	@echo "removed: $(DEST)/$(SKILL)"

check:
	@./tests/validate-skill.sh
	@python3 tests/run-script-tests.py

test: check

# The zip root is the skill folder itself — the layout claude.ai accepts
# and the one that unzips cleanly into ~/.claude/skills/ (ADR-004).
package: check
	@rm -rf dist
	@mkdir -p dist
	@zip -qr "dist/$(SKILL)-$(VERSION).zip" "$(SKILL)" -x '*.DS_Store' -x '*__pycache__*'
	@echo "packaged: dist/$(SKILL)-$(VERSION).zip"

clean:
	@rm -rf dist
