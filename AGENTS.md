# AGENTS.md — news-digest

## Project summary

Claude Code Skill that collects a user's own RSS/Atom/JSON feeds, scores each
article on the active profile's axes (`novelty` / `significance` /
`relevance`), derives a reading priority from that profile's decision table,
tracks continuing stories so that a genuinely new event is distinguished from
a rehash with no new facts, and compiles a digest of only what is worth
reading. Invoked as `/news-digest --repo <corpus-path>`.

The repository holds the engine only. Configuration and the article corpus
live in a separate private *corpus repository* joined to the skill by a
`.newsrc.toml` contract — see
[ADR-0001](docs/en/adr/0001-public-engine-private-corpus.md). Domain knowledge
is not in the code: it lives in swappable profiles.

Python 3.11+, standard library only. No service, no database, no credentials.

## Key commands

| Command | Purpose |
|---------|---------|
| `make check` (= `make test`) | Structural validation of the skill |
| `make install` | Copy the skill to `~/.claude/skills/news-digest` |
| `make package` | Build `dist/news-digest-vX.Y.Z.zip` (zip root is the skill folder) |
| `make uninstall` | Remove the installed copy |
| `make clean` | Remove `dist/` |

## Structure

```
news-digest/            The skill — the only thing `make package` ships
├── SKILL.md            Phase 0–9 pipeline; frontmatter name must equal the directory
├── references/
│   └── data-model.md   Record shapes, identity rule, re-analysis queries
├── scripts/lib/        corpus.py (.newsrc.toml contract, version gate),
│                       profile.py (axes, extends chain, decision table),
│                       records.py (identity and text normalization)
├── scripts/collectors/ (next) rss / jsonfeed / html / json_api + shared HTTP layer
├── scripts/            (next) parse_args, check_config, collect, prefilter,
│                       apply_table, merge, build_digest, compile, to_notify, validate
└── profiles/           generic; security-news extends it
tests/
├── validate-skill.sh   Byte-identical vendored copy of .github/templates/ (ADR-006)
└── run-script-tests.py Behaviour tests, hooked into the Makefile check target
examples/config/        (P5) sanitized starting configuration — placeholders only
docs/{en,ja}/adr/       Design records
```

`docs/design/` is gitignored: it holds the Phase 1 planning artifact, whose
public form is ADR-0001. Two copies of a design drift.

## Gotchas

- **`tests/validate-skill.sh` must stay byte-identical** to
  `.github/templates/validate-skill.sh`; `check-org.sh` check 10b fails on
  drift. To change the validator, edit the canonical copy and re-vendor.
  Repo-specific tests go in the Makefile `check` target, never in this file.
- **The validator resolves relative Markdown links** inside the skill
  directory. Linking to a reference file before creating it fails `make check`.
- **`id = sha1(canonical_url)`.** Changing `canonicalize_url` is a breaking
  change to every corpus in existence; it needs a `schema_version` bump and a
  documented rebuild.
- **The agent must not write a priority.** It writes axis scores;
  `apply_table.py` derives priority from the profile's decision table. This is
  why the triage schema has no priority field.
- **Merge must remain idempotent** — a partially failed run is re-run from
  Phase 4.
- **Nothing that names a real feed set, organization, or messaging
  destination belongs in this repository.** Those live in corpus
  repositories; `examples/` uses placeholders. The predecessor was
  unpublishable precisely because they were mixed together.
- **Scripts cannot send messages.** `to_notify.py` splits the text and stops;
  the agent resolves a messaging tool at run time. Do not add a transport
  dependency to the scripts.
- **Standard library only.** Adding a dependency defeats the reason the tool
  exists.

## Status

In development. The corpus contract, the profile mechanism, and the shipped
profiles are implemented and tested; collection and the rest of the pipeline
are next. Both READMEs carry a status notice that must be removed before
tagging 0.1.0.
