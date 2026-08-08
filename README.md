# news-digest

A Claude Code Skill that collects your own feeds, decides what is worth
reading, and compiles a digest of only that.

Feeds forwarded into a chat channel produce 130–180 items a day, which means
nobody reads any of them. This skill draws three distinctions instead:

- **Is it worth reading?** — the significance of the event and your own
  relationship to it are scored separately
- **Is it new?** — each article is checked against the continuing stories you
  have already been following
- **Has it gone stale?** — topics that turned up again with no new facts are
  named as such

The third one is the point: it leaves behind a record of *why something did
not need reading*.

## Code here, data elsewhere

This repository holds the engine and nothing else. Your feed list, your
relevance profile, and the article corpus live in a separate **corpus
repository** that you own and keep private — it names what you watch and what
you care about, which is not something to publish.

```
news-digest (public)                  your-corpus (private)
  skill, scripts, profiles,             config/  data/  digests/
  examples, tests                       .newsrc.toml
```

The corpus repository contains no code, and one installed skill can serve
several corpora. `.newsrc.toml` is the contract between them: it declares the
config and schema versions, the evaluation profile, and where digests are
sent. The skill refuses to run against a version it does not support, so an
engine update cannot quietly corrupt a corpus.

## Profiles

The engine knows nothing about any subject area. A **profile** supplies the
evaluation axes, the decision table that turns axis scores into a reading
priority, the rubric the agent reads before scoring, and the digest layout.

Three axes ship as the base, kept deliberately domain-neutral because their
frames of reference are orthogonal:

| Axis | The question | Frame |
|---|---|---|
| `novelty` | What does this add to what is already known? | time |
| `significance` | How big is this, with you removed from the picture? | the world |
| `relevance` | How directly does it touch you? | you |

`security-news` extends the base profile, overriding the rubric, the decision
table, and the shape of `interests.toml`. Axis IDs are shared vocabulary
across profiles, so a tuned rubric never makes the accumulated corpus
incomparable with itself.

**The agent never writes a priority.** It scores the axes; a script applies
the decision table. A rule that cannot be violated does not need to be
enforced.

## Install

Download `news-digest-vX.Y.Z.zip` from
[Releases](https://github.com/nlink-jp/news-digest/releases), then register it:

- **In the app** (Claude Desktop, claude.ai, mobile) — add the zip from the
  skill settings (Customize → Skills). Prefer this route; it survives changes
  to where skills are stored on disk.
- **Claude Code** — `unzip news-digest-vX.Y.Z.zip -d ~/.claude/skills/`, or into a
  project's `.claude/skills/` for a project-scoped install.

From a checkout:

```bash
make install
```

That builds the release zip and unpacks *that*, so what you run is what a
release ships — a packaging defect breaks your install rather than reaching
users. `make install DEST=/path/to/skills` installs elsewhere;
`make uninstall` removes it.

## Usage

```bash
/news-digest --repo ~/path/to/your-corpus
/news-digest --repo ~/path/to/your-corpus --since 2026-08-01 --dry-run
/news-digest --repo ~/path/to/your-corpus --source jpcert
```

| Argument | Default | Meaning |
|---|---|---|
| `--repo <path>` | current directory | The corpus repository |
| `--since` / `--until` | previous day 00:00 / now | Collection window |
| `--source <id>` | all sources | Restrict to specific sources (repeatable) |
| `--dry-run` | — | Neither notify nor commit |
| `--no-post` / `--no-commit` | — | Suppress one or the other |

A ready-to-copy corpus template is in `news-digest/examples/corpus/`; see
`news-digest/references/setting-up-a-corpus.md`.

## Requirements

Python 3.11 or later, and nothing else. The scripts use only the standard
library — no package installation, no service, no database. Feed collection
needs no credentials; sources that require a token reference an environment
variable by name, never a literal.

Sending a digest uses whatever messaging tool is available to the agent at
run time. The configuration names a destination, not a transport, so there is
no dependency on any particular local CLI or MCP server; if nothing suitable
is available, the run completes and says so.

## What it does not do

No web UI. No full-text archive — only summaries of what you marked as
must-read are kept. No crawling beyond the feeds you registered and the
articles you chose to read. Feeds and article bodies are treated as untrusted
throughout: article text reaches the agent inside nonce-delimited tags, and
URLs appearing inside an article are never fetched.

## Development

```bash
make check      # structural validation (= make test)
make install    # copy the skill to ~/.claude/skills/news-digest
make package    # build dist/news-digest-vX.Y.Z.zip
```

Design records are in `docs/en/adr/` (Japanese: `docs/ja/adr/`).

日本語版は [README.ja.md](README.ja.md) にあります。

## License

MIT — see [LICENSE](LICENSE).

[releases]: https://github.com/nlink-jp/news-digest/releases
