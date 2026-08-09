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
│   ├── data-model.md   Record shapes, identity rule, re-analysis queries
│   ├── setting-up-a-corpus.md   Agent-runnable setup runbook; the interview
│   │                   step marks what only the user can decide
│   └── scheduled-operation.md   Unattended-run contract, schedule recipes,
│                       diagnosis order when digests stop arriving
├── examples/corpus/    Ready-to-copy corpus template (placeholders only)
├── scripts/lib/        corpus (contract + version gate), profile (axes,
│                       extends chain, decision table), records (identity),
│                       http (the only network access), sources, filters,
│                       seen, stories, state, window, triage
├── scripts/collectors/ rss (RSS 2.0 / Atom / RDF), jsonfeed. They parse; they
│                       never fetch — see the gotcha below
└── profiles/           generic; security-news extends it
tests/
├── validate-skill.sh   Byte-identical vendored copy of .github/templates/ (ADR-006)
└── run-script-tests.py Behaviour tests, hooked into the Makefile check target
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
- **`id = sha1(canonical_key)`.** Changing `canonical_key` is a breaking
  change to every corpus in existence; it needs a `schema_version` bump and a
  documented rebuild.
- **`canonical_key` is scheme-less and not fetchable.** Fetch `url`. Passing
  the key to a fetcher is the mistake the name exists to prevent.
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
- **Collectors parse; `lib/http.py` fetches.** That split is what makes
  conditional GET, the size cap, backoff, and the redirect policy hold for
  every source type. A collector that fetches has routed around all of it.
- **`compile.py` renders per destination flavour.** The file and the message
  must carry the same *content*, not the same bytes — an inline Markdown link
  reached Slack as a title with no address. A new flavour must keep every
  item's URL; there is a test for exactly that.
- **A caveat states what it changes for the reader.** An observation about a
  feed's behaviour is maintenance and belongs in the run's report.

## Pipeline

`parse_args` → `check_config` → `collect` → `prefilter` → **[agent scores]** →
`apply_table` → `merge` → **[agent summarizes]** → `validate` → `build_digest`
→ `compile` → `to_notify` → **[agent sends]**.

Judgement lives in exactly the two bracketed steps. Everything else is a
script, and a change that moves work into a bracket is a change to the
design, not an implementation detail.
