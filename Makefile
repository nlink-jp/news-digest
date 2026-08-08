SKILL   := news-digest
VERSION ?= $(shell git describe --tags --always --dirty 2>/dev/null || echo dev)
DEST    ?= $(HOME)/.claude/skills

.PHONY: install uninstall check test package clean

install:
	@mkdir -p "$(DEST)/$(SKILL)"
	@cp -R "$(SKILL)/." "$(DEST)/$(SKILL)/"
	@echo "installed: $(SKILL) -> $(DEST)/$(SKILL)"

uninstall:
	@rm -rf "$(DEST)/$(SKILL)"
	@echo "removed: $(DEST)/$(SKILL)"

check:
	@./tests/validate-skill.sh

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
