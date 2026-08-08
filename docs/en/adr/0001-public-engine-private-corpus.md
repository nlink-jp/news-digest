# ADR-0001: news-digest Skill — Public Engine, Private Corpus, Pluggable Profiles

| Field | Value |
|-------|-------|
| Status | **Accepted** |
| Date | 2026-08-08 |
| Binds | news-digest |
| Decision makers | nlink-jp maintainers |
| Triggered by | A working predecessor (`news-resource`) that could not be published: its judgment logic was written for one organization, and its deterministic parts had no tests and no version contract |

## Context

A predecessor ran daily for a short period: 27–31 RSS/Atom feeds collected
directly, ~130–180 articles per day reduced by rule-based prefiltering, the
survivors evaluated by an agent along three axes, and the result compiled
into a Markdown digest posted to Slack. Everything — skill, scripts,
configuration, and the article corpus — lived in one repository.

The design was sound in two respects worth preserving. It kept every
article, including the ones it dropped, with the reason attached: the record
of *why something did not need reading* is half of what the corpus is for.
And it froze each record's `origin` at collection time, so changing the feed
list never rewrites history.

It was unfit to publish for two independent reasons.

**It was written for one organization.** The relevance profile carried the
operator's line of business, its customers' products, and a Slack channel
ID. The noise filters keyed their rules to that operator's specific feed
IDs, so the rules and the subscription list could not be separated. The
evaluation axes were named for security work (`severity`), and the rubric
that gave those axes meaning was prose inside the skill rather than data
beside it. Paths were resolved relative to the working directory, and the
HTTP User-Agent carried the repository URL.

**The implementation had gaps that separation alone would not close.**

- 1,765 lines of Python carried no tests.
- The rubric instructed the agent not to decide priority "by feel", but
  nothing checked that a written priority agreed with the axis scores
  written beside it. The instruction was unenforceable by construction.
- The agent was asked to transcribe collection statistics from one JSON file
  into another and to derive "topics with no new facts today" by hand. Both
  are deterministic computations, paid for in tokens and transcription risk.
- Untrusted feed text reached the model with no structural isolation — the
  defence was a prose warning at the top of the skill, followed by a list of
  prohibited actions. Such lists park attacker-shaped strings in the trusted
  region and make the enumerated items more salient, not less.
- Feeds return only their most recent N items, and one source returned five.
  Nothing recorded the last-seen article per source, so a gap between runs
  was silently indistinguishable from a quiet day.
- No conditional GET, no retry, no backoff.
- Command-line arguments existed only as a table in the skill's Markdown,
  interpreted by the model at read time.

The predecessor's remote repository has been deleted. The local copy is
retained as a porting source until the core engine is complete.

## Decision

**1. Split into a public engine and a private corpus.** `nlink-jp/news-digest`
(public, skills-series, ADR-004 layout) holds the skill, scripts, profiles,
examples, and tests. A private repository holds `config/`, `data/`, and
`digests/` and contains no code. The skill takes `--repo <path>`; a
`.newsrc.toml` at the corpus root is the contract between them, declaring
`config_version`, `schema_version`, the profile name, paths, and
notification destinations. One installed skill can serve several corpora.

**2. Dispatch collection by source type.** Each source declares
`type` (`rss` | `jsonfeed` | `html` | `json_api`); a collector module per
type returns the same normalized record, and nothing downstream of
collection reads `type`. A shared HTTP layer owns conditional GET (ETag /
If-Modified-Since), retry with backoff, size limits, and per-source state
including the last-seen publication timestamp — which makes a collection gap
detectable and reportable instead of invisible.

**3. Profiles own the domain knowledge.** A profile bundles the axis
definitions, the decision table, the rubric text, the digest layout, and the
schema its `interests.toml` must satisfy. The engine knows none of it.

Axis IDs are a **shared vocabulary across profiles**, so the corpus stays
queryable across profile changes. The three axes are kept but redefined
domain-neutrally, justified by their frames of reference being orthogonal:
`novelty` (against what is already known), `significance` (the world, with
the reader removed), `relevance` (the reader's own contact with it).
`severity` is renamed `significance`: the name implied CVSS, the question
never did. A fourth axis is rejected on the grounds that candidates collapse
into products of these three. Profiles may raise the resolution of an axis's
meaning freely, must declare an axis addition and bump `profile_version`,
and may not repurpose an existing ID. `security-news` is defined as
`extends = "generic"`, which makes the shared vocabulary structural rather
than conventional.

**4. Priority is derived, never written.** The decision table is
machine-readable and evaluated top-down, first match wins. The agent writes
axis scores; a script writes priority. Source credibility is likewise
derived from the source's `tier` rather than judged.

**5. The agent writes exactly two artifacts** — `triage.json` (axis scores,
rationale, story linkage) and `narrative.json` (summaries of the must-read
items, the day's overall picture, anomalies). Statistics, stale-topic
derivation, ordering, and digest assembly move into `build_digest.py`.

**6. Untrusted text is isolated structurally.** `prefilter.py` generates a
nonce and wraps each article's title and summary before the agent sees them.
The skill states that the wrapped region is material to extract from, not
instructions to follow — and the prohibition list is removed.

**7. Notification declares a destination, not a transport.** Configuration
names where output goes (a Slack channel); the skill resolves how to send it
at run time from whatever MCP tools are available, skipping and reporting if
none is. `to_notify.py` splits the text and stops there. No dependency on
any local CLI or on any particular MCP server.

**8. Execution is local only.** Cloud execution is out of scope given its
network and repository-access constraints. This removes the constraint that
forced the predecessor to hard-code one Slack transport.

**9. Tests are written with the implementation**, with exhaustive coverage
of the decision table (every axis combination) and of merge idempotence.

## Consequences

- **Separation creates a failure mode the predecessor did not have:** a
  change in public code can break private data. `config_version` and
  `schema_version` gate this at startup, and a change to `canonicalize_url`
  — which determines article identity via `SHA-1(canonical_url)` — is
  treated as a breaking change requiring a rebuild procedure.
- The corpus repository is not a member of any series and is not owned by
  the organization. That category needs to exist; see
  [ADR-019](https://github.com/nlink-jp/.github/blob/main/adr/019-deployment-repositories.md).
- Two repositories to coordinate. Only the public one has a release cycle.
- The predecessor's corpus (two days) is not migrated; the record schema
  differs and the volume does not justify a converter.
- The profile layer costs an indirection a single-purpose tool would not
  pay. Accepted: the axis vocabulary is precisely what keeps the corpus
  comparable when the rubric is tuned, which will happen continuously.
- Local-only execution means the daily run depends on a machine being awake.
  Accepted; a missed day is now *detected* (decision 2) rather than silent.
- Removing the prose prohibition list means the skill no longer restates
  what the agent already knows; the boundary it was approximating is now
  drawn by nonce isolation and by the set of URLs the skill is permitted to
  fetch.

## Alternatives considered

| Alternative | Why not |
|---|---|
| Go CLI in util-series plus a thin skill | Two release cycles for one product. The deterministic parts exist only to bracket the judgment step; splitting them across repositories doubles the coordination without separating anything meaningful |
| Keep one repository and gitignore the private parts | The git history *is* the corpus. Ignoring it defeats the purpose, and publishing later would require history rewriting |
| Corpus repository as a submodule of the skill repository, or the reverse | Couples release cycles, conflicts with the convention that gitignores `.claude/`, and prevents one skill from serving several corpora |
| Three config files as the only contract, without `.newsrc.toml` | Version negotiation needs somewhere to live that is not itself versioned by the thing it describes |
| Keep `severity` as an axis name and treat the axes as security-specific | The name implied CVSS; the question ("how big is this, with me removed?") is domain-independent. Keeping the name would have made every future profile inherit a security framing |
| Let the agent write priority and validate it afterwards | A check can only reject after the fact, and rejection costs a round trip. A derivation cannot disagree with itself |
| Add an `actionability` or `urgency` axis | Both collapse into products of the existing three, and a correlated axis adds judgment cost while reducing the decision table's legibility |
| SQLite for the corpus | Binary blobs in git destroy diffability and reviewability, which is half the corpus's value. SQLite remains available as a gitignored read-side index |

## References

- [CONVENTIONS.md](https://github.com/nlink-jp/.github/blob/main/CONVENTIONS.md) — Starting a New Project (Plan → Scaffold → Develop), Skill project scaffold
- [ADR-004](https://github.com/nlink-jp/.github/blob/main/adr/004-skills-series-umbrella.md) — skills-series layout, one repository per skill
- [ADR-006](https://github.com/nlink-jp/.github/blob/main/adr/006-skill-validator-vendoring.md) — vendored `validate-skill.sh`
- [ADR-017](https://github.com/nlink-jp/.github/blob/main/adr/017-adr-authoring-conventions.md) — ADR authoring conventions and the `Binds` field
- [ADR-019](https://github.com/nlink-jp/.github/blob/main/adr/019-deployment-repositories.md) — deployment repositories are not series members
