#!/usr/bin/env bash
#
# Structural validation for the skill in this repository.
#
# Skills are Markdown, so there is no behaviour to unit-test — but a
# multi-file skill can still rot: a renamed reference file, a typo'd link, a
# frontmatter name that no longer matches its directory (which silently breaks
# the slash command). This checks exactly those things.
#
# Vendored per skill repository as tests/validate-skill.sh; the canonical copy
# lives in nlink-jp/.github templates/ (ADR-006). check-org.sh keeps the copies
# byte-identical — edit the canonical, then re-vendor into every skill repo.
# Repo-specific tests belong in the Makefile check target, not in this file.
#
# Usage: make check
set -euo pipefail

cd "$(dirname "$0")/.."

errors=$(mktemp)
trap 'rm -f "$errors"' EXIT

err() { printf 'FAIL: %s\n' "$1" >>"$errors"; }

skills=$(find . -mindepth 2 -maxdepth 2 -name SKILL.md | sort)
if [ -z "$skills" ]; then
	echo "FAIL: no SKILL.md found" >&2
	exit 1
fi

for skill_md in $skills; do
	dir=$(dirname "$skill_md")
	skill=$(basename "$dir")
	printf 'checking %s\n' "$skill"

	# --- frontmatter ---
	if [ "$(head -1 "$skill_md")" != "---" ]; then
		err "$skill: SKILL.md does not start with YAML frontmatter"
		continue
	fi
	frontmatter=$(awk 'NR > 1 { if ($0 == "---") exit; print }' "$skill_md")
	name=$(printf '%s\n' "$frontmatter" | sed -n 's/^name:[[:space:]]*//p')
	description=$(printf '%s\n' "$frontmatter" | sed -n 's/^description:[[:space:]]*//p')

	[ -n "$name" ] || err "$skill: frontmatter has no name:"
	[ -n "$description" ] || err "$skill: frontmatter has no description:"
	if [ -n "$name" ] && [ "$name" != "$skill" ]; then
		err "$skill: frontmatter name '$name' does not match the directory name (the directory name is the slash command)"
	fi
	case "$skill" in
	*[!a-z0-9-]*) err "$skill: skill name must be lowercase letters, digits, and hyphens only" ;;
	esac

	# --- relative links resolve ---
	while IFS= read -r md; do
		while IFS= read -r link; do
			case "$link" in
			http*/* | http* | mailto:* | '#'*) continue ;;
			esac
			target=${link%%#*}
			[ -n "$target" ] || continue
			[ -e "$(dirname "$md")/$target" ] ||
				err "$md: broken link -> $link"
		done < <(grep -o '](\([^)]*\))' "$md" | sed 's/^](//; s/)$//')
	done < <(find "$dir" -name '*.md' | sort)
done

if [ -s "$errors" ]; then
	cat "$errors" >&2
	printf '\n%s error(s)\n' "$(wc -l <"$errors" | tr -d ' ')" >&2
	exit 1
fi

echo "skill OK"
