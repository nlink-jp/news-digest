# Setting up a corpus

A corpus is a private repository holding your feed list, your relevance
profile, and everything collected. The skill holds no data and never assumes
where a corpus is; you pass one with `--repo`.

A ready-to-copy template is in [`examples/corpus/`](../examples/corpus/).

## Start

```bash
mkdir my-news && cd my-news
git init
cp -R <skill>/examples/corpus/. .
```

That gives you `.newsrc.toml` and `config/`. `data/` and `digests/` are
created on the first run.

Then edit three files, in this order of importance:

1. **`config/interests.toml`** — who is reading and what for. The relevance
   axis is scored against this, so it sets the accuracy of the digest more
   than anything else. The template is placeholders; replace all of them.
2. **`config/sources.toml`** — the feeds. Give each a `category` you would
   want to filter as a group, and a `tier` describing how it stands to the
   facts it reports.
3. **`config/filters.toml`** — mechanical noise removal. Start with the
   template and tighten it once you have seen a week of digests.

Check the result before collecting anything:

```bash
python3 <skill>/scripts/check_config.py --repo . --probe
```

`--probe` connects to each feed and confirms something parseable comes back.
Every `ERROR` is a thing that would otherwise have produced a plausible, wrong
digest — most often a run reporting "collected 0 articles" on a day that was
not quiet.

## Keep it private

`config/interests.toml` describes what your organization does and what it
runs. `data/` accumulates what you watch. Neither belongs in a public
repository, and a notification channel identifier belongs only here.

## Choosing a profile

`.newsrc.toml` names an evaluation profile.

- **`generic`** — the domain-neutral base. Three axes: `novelty` (against what
  is already known), `significance` (the world with you removed), `relevance`
  (your own contact with it).
- **`security-news`** — extends `generic`, inheriting the axes and replacing
  the rubric, the decision table, and what `interests.toml` must provide.
  Reaching `must_read` through significance additionally requires a source
  that establishes facts, because security coverage re-reports one event
  across many outlets within hours.

A profile states the shape its `interests.toml` must have, and
`check_config.py` enforces it. Changing profiles usually means editing
`interests.toml` too.

Axis IDs are shared between profiles on purpose: tuning a rubric, or moving
from `generic` to `security-news`, leaves everything already collected
comparable with what comes after.

## Calibrating

Expect the first weeks to be calibration.

- Something surfaced that you did not want → the fix is usually in
  `interests.toml`, not the filters. Check `topics.critical` first; it is the
  easiest place to over-promote.
- Something you wanted was missing → look at its `prefilter` verdict in
  `data/articles/`. If a rule dropped it, add a pattern to `[keep]`.
- The must-read list is too long to trust → `topics.critical` is doing too
  much, or `stack.watch_products` names things you do not actually operate.

After a few months, the corpus can answer which feeds earn their place:

```bash
cat data/articles/*/*/*.jsonl \
  | jq -r 'select(.triage) | "\(.origin.source_id)\t\(.triage.priority)"' \
  | sort | uniq -c
```

A source that has never produced a `must_read` is a candidate for
`enabled = false` — or for a `[gate]`, if it is occasionally the first to
report something.

## Versions

`.newsrc.toml` declares `config_version` and `schema_version`. The skill
refuses to run against versions it does not support, rather than writing
records the corpus cannot read back. Do not raise them by hand; an upgrade
that needs them raised will say so and describe the migration.
