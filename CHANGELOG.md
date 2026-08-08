# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/).

## [0.1.0] - 2026-08-08

### Added

- Initial release. Claude Code Skill successor to an unpublished predecessor
  whose judgement logic was written for one organization and whose corpus
  shared a repository with its code
  ([ADR-0001](docs/en/adr/0001-public-engine-private-corpus.md)).
- Public engine, private corpus. The skill holds no data; feed lists,
  relevance profiles, and article records live in a separate corpus
  repository passed as `--repo`, joined by a `.newsrc.toml` contract that
  declares `config_version`, `schema_version`, the profile, and the digest
  destinations. `scripts/check_config.py` refuses to run outside the
  supported version range, so an engine update cannot corrupt a corpus it
  cannot see. One installed skill serves any number of corpora.
- Swappable evaluation profiles. A profile supplies the axes, the decision
  table, the rubric read before scoring, the digest layout, and the schema
  its `interests.toml` must satisfy; the engine knows none of it. Ships
  `generic` (`novelty` / `significance` / `relevance`, deliberately
  domain-neutral) and `security-news`, which extends it. Axis IDs are shared
  vocabulary, so tuning a rubric leaves the accumulated corpus comparable
  with itself.
- `scripts/apply_table.py` — reading priority is *derived* from the profile's
  decision table. The agent scores axes and writes no priority, so a score
  and its verdict cannot disagree.
- Collection dispatched by source type (`rss`, `jsonfeed`, `html`,
  `json_api`) over a shared HTTP layer with conditional GET, backoff, size
  caps, and per-source state. A window opening after a source's last-seen
  article is reported as a collection gap rather than passing for a quiet
  day. Sources needing a token reference an environment variable by name.
- Untrusted feed text is isolated structurally: `scripts/prefilter.py` wraps
  article titles and summaries in nonce-delimited tags before the agent sees
  them, the XML parser rejects documents carrying a DTD, and body size is
  capped. The only URLs fetched are the configured feeds and the articles
  scored as must-read.
- Continuing stories and stale-topic reporting: articles are matched against
  open stories, and a story whose articles all scored `novelty: 0` on a given
  day is named as stale — the record of why something did not need reading.
- Every collected article is stored with its `origin` frozen at collection
  time, including articles the prefilter dropped, with the verdict and rule
  that dropped them (`references/data-model.md`).
- `scripts/build_digest.py` and `scripts/compile.py` — statistics, stale-topic
  derivation, ordering, layout, and rendering are deterministic; the agent
  contributes only axis scores and the must-read summaries.
- `scripts/to_notify.py` splits the digest to a destination's size limit;
  sending uses whatever messaging tool is available at run time. The
  configuration names a destination, not a transport, so there is no
  dependency on any local CLI or MCP server.
- Python 3.11+, standard library only — no package installation, service, or
  database.
- `make package` — builds `dist/news-digest-vX.Y.Z.zip` with the skill folder
  at the zip root, ready to attach to a GitHub Release, unzip into
  `~/.claude/skills/`, or upload to claude.ai (Settings → Skills).
