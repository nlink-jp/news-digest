# Data model

Every record shape the corpus repository stores, and the two files the agent
writes. All timestamps are ISO 8601 with an explicit offset.

The corpus is plain text on purpose: JSONL and TSV diff and review cleanly in
git, and half of what makes the corpus valuable is being able to read *why* an
article was dropped months later. A binary store would forfeit that.

---

## Identity

```
id = "sha1:" + sha1(canonical_key)[:16]
```

The algorithm prefix is part of the value: the identity rule is the one thing
a corpus cannot silently change, so the data says which rule produced it.

A record keeps the address twice. **`url`** is exactly what the feed gave, and
it is the only one that is fetchable. **`canonical_key`** exists solely to
answer "have we seen this before" — a scheme-less string such as
`example.com/post/1?id=7`.

Separating the two is what lets the key be aggressive. It discards the
scheme, a leading `www.`, host letter case, a default port, tracking
parameters, the fragment, and a trailing slash, and it sorts the surviving
query parameters. None of that distinguishes one article from another, and
keeping the scheme in particular would mean a site's migration to HTTPS
republished its entire archive into one day's digest.

Path letter case is preserved: paths are case-sensitive, and two that differ
may well be two articles.

**Changing the normalization rules is a breaking change.** Past `id` values
will no longer match, which severs the seen index and every story reference.
A change requires a `schema_version` bump and a documented rebuild procedure.

---

## Article — `data/articles/YYYY/MM/DD.jsonl`

One JSON object per line, partitioned by publication date. Every collected
article is stored, including those the prefilter dropped and those that
overflowed the candidate limit.

| Field | Type | Notes |
|---|---|---|
| `id` | string | `sha1:<16 hex>` of `canonical_key` |
| `schema_version` | int | The version this record was written under |
| `canonical_key` | string | Comparison key. Scheme-less — never fetch this |
| `url` | string | The address as the feed gave it. Fetch this |
| `title` | string | |
| `summary` | string \| null | Feed excerpt, truncated |
| `published_at` | string | From the feed |
| `collected_at` | string | When this run fetched it |
| `origin` | object | See below |
| `prefilter` | object | `{verdict, rule_id, reason}` — `verdict` is `candidate` \| `drop` \| `overflow` |
| `triage` | object \| null | Present only for scored articles. See below |

### `origin`

Copied from `config/sources.toml` **at collection time and never rewritten**.
Editing the source list does not alter past records; that is what makes the
corpus traceable.

| Field | Notes |
|---|---|
| `source_id`, `source_name`, `feed_url` | |
| `collector` | The collector type that produced the record (`rss`, `jsonfeed`, …) |
| `category`, `lang`, `tier`, `weight` | As configured at the time |
| `body_fetchable` | Whether this source serves article bodies to this tool. Absent on records written before the field existed; read it as true |

### `triage`

| Field | Notes |
|---|---|
| `profile` | Profile name |
| `profile_version` | Bumped when the profile's axis set changes |
| `axes` | Object keyed by axis id, integer scores |
| `priority` | **Derived** from the decision table — never written by the agent |
| `credibility` | Derived from `origin.tier` |
| `why` | A fact specific to this article |
| `story_id` | string \| null |
| `deep_read` | Whether the body was read |
| `body_summary` | string \| null — present only when `deep_read` is true |

---

## Story — `data/stories/story-YYYY-NNNN.json`

A continuing matter that spans articles and days.

| Field | Notes |
|---|---|
| `id`, `title`, `slug` | |
| `status` | `open` \| `dormant` \| `closed` |
| `created_at`, `updated_at` | |
| `timeline` | Array of `{date, article_id, source, title, note}`, oldest first |
| `article_ids` | Every article attached to this story |

A story is what makes "no new facts today" expressible: when all of a story's
articles on a given day scored `novelty: 0`, the digest names it as stale
rather than repeating it.

---

## Seen index — `data/index/seen-YYYY.tsv`

`id`, `canonical_key`, `first_seen_at`, `source_id` — tab-separated, one line
per article, append-ordered and split by year so a run appends rather than
rewriting the whole file.

## Source state — `data/state/sources.json`

Keyed by source id. Carries what the next run needs in order to be polite and
to notice what it missed.

| Field | Notes |
|---|---|
| `etag`, `last_modified` | Replayed as conditional-GET headers |
| `last_seen_published_at` | The newest article seen from this source |
| `last_fetch_at`, `last_status` | |
| `consecutive_errors` | Drives backoff and the "this feed may be dead" warning |

`last_seen_published_at` is what makes a collection gap detectable: feeds
return only their most recent N items, so a window that opens after that
timestamp means articles were published and are already gone.

---

## Agent-written files

These two are the entire judgement surface. Everything else in the pipeline
is computed.

### `triage.json`

An array, one entry per candidate: `id` (copied verbatim), `axes` (a score
per axis declared by the profile), `why`, and optionally `story_id` or
`new_story`. **There is no priority field** — priority is derived in Phase 4.

### `narrative.json`

`items` — for each deep-read article, `{id, summary, deep_read}`;
`natural_language_summary` — 3–6 sentences on the day as a whole;
`anomalies` — an array of observed facts (stopped feeds, collection gaps,
failed fetches, text addressed to the agent inside article content).

---

## Digest — `digests/YYYY-MM-DD.{json,md}`

The JSON is the structured form and the Markdown is rendered from it by
`compile.py`; the same renderer produces the text that gets sent, so the two
never disagree. To change the wording, change `compile.py`.

Sections, ordering, and which priorities appear are laid out by the profile,
not hard-coded.

---

## Re-analysis

The point of keeping everything is being able to ask a different question
later.

```bash
# Everything that became must-read, in order
cat data/articles/2026/*/*.jsonl | jq -c 'select(.triage.priority == "must_read")'

# How one story unfolded
jq -r '.timeline[] | "\(.date) [\(.source)] \(.title)"' data/stories/story-2026-0001.json

# Must-read rate per source — the cost-effectiveness of each feed
cat data/articles/2026/*/*.jsonl \
  | jq -r 'select(.triage) | "\(.origin.source_id)\t\(.triage.priority)"' \
  | sort | uniq -c
```

Run the last query over a few months to set `weight` from measurement, and to
find feeds that have never produced a must-read.
