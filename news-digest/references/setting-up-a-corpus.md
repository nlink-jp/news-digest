# Setting up a corpus

A corpus is a private repository holding your feed list, your relevance
profile, and everything collected. The skill holds no data and never assumes
where a corpus is; you pass one with `--repo`.

This file is a runbook: an agent can execute it end-to-end. The boundary to
respect is that **the corpus's content comes from the user**. What they care
about, which feeds they trust, where the digest goes — none of it can be
invented. Step 1 is a conversation; everything after it is mechanics.

A ready-to-copy template is in [`examples/corpus/`](../examples/corpus/).

## 1 — Collect the decisions only the user can make

Ask, and do not proceed on guesses:

- **Who is reading, and what for.** Their role, what they operate or depend
  on, what they must not miss. This becomes `config/interests.toml`, and the
  relevance axis is scored against it — it sets the accuracy of the digest
  more than anything else does.
- **The feeds.** Which sources they already read or trust. Suggesting
  candidates is fine; adding a feed the user never agreed to is not.
- **The profile.** `generic` or `security-news` — see "Choosing a profile"
  below.
- **The destination.** A channel to send the digest to, or none — digests are
  still written to the corpus either way.
- **Where the corpus lives.** A directory path, and whether to create a
  private remote for it (recommended; required before scheduling).

A template placeholder left in place does not fail loudly — it produces a
digest quietly scored against the wrong interests. Every placeholder is
replaced or removed in step 3.

## 2 — Scaffold

```bash
mkdir my-news && cd my-news
git init
cp -R <skill>/examples/corpus/. .
```

That gives you `.newsrc.toml` and `config/`. `data/` and `digests/` are
created on the first run.

## 3 — Configure

Four files, in this order of importance:

1. **`config/interests.toml`** — who is reading and what for. The template is
   placeholders; replace all of them.
2. **`config/sources.toml`** — the feeds. Give each a `category` you would
   want to filter as a group, and a `tier` describing how it stands to the
   facts it reports.
3. **`.newsrc.toml`** — the profile, and the `[[notify]]` destination. Put the
   real channel identifier here (it belongs only in this private repository),
   or delete the section to generate digests without sending them. A
   placeholder channel ID left in place fails every later run at the send
   step.
4. **`config/filters.toml`** — mechanical noise removal. Keep the template
   as-is and tighten it once you have seen a week of digests.

## 4 — Verify before collecting anything

```bash
python3 <skill>/scripts/check_config.py --repo . --probe
```

`--probe` connects to each feed and confirms something parseable comes back.
Every `ERROR` is a thing that would otherwise have produced a plausible, wrong
digest — most often a run reporting "collected 0 articles" on a day that was
not quiet. Fix and re-run until clean. A `WARNING` may stand, but tell the
user about it.

## 5 — Create the private remote

`config/interests.toml` describes what your organization does and what it
runs. `data/` accumulates what you watch. Neither belongs in a public
repository.

```bash
git add -A
git commit -m "chore: initial corpus configuration"
gh repo create <owner>/<name> --private --source . --push
```

Then verify what was actually created — do not assume:

```bash
gh repo view <owner>/<name> --json visibility
```

Anything but `PRIVATE`: stop and fix it before the first run writes data. A
corpus that must be published later needs its history scrubbed, not just its
head.

A corpus without a remote still works — the run's push step fails and is
reported, nothing else changes — but add the remote before scheduling
unattended runs.

## 6 — First run, supervised

Run once interactively before trusting the schedule with it:

```bash
/news-digest --repo <path> --dry-run
```

`--dry-run` neither notifies nor commits, so a first run against badly tuned
interests costs nothing. Read the digest it writes, adjust (see "Calibrating"
below), then run without `--dry-run`.

When digests should start arriving on their own,
[scheduled-operation.md](scheduled-operation.md) continues from here — after,
not before, a supervised run has produced a digest the user believes.

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
