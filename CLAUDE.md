# CLAUDE.md — news-digest

**Organization rules: https://github.com/nlink-jp/.github/blob/main/CONVENTIONS.md**

Design record: [`docs/en/adr/0001-public-engine-private-corpus.md`](docs/en/adr/0001-public-engine-private-corpus.md)
(日本語: [`docs/ja/`](docs/ja/adr/0001-public-engine-private-corpus.ja.md)).

## What this repository is

The engine only. Feed lists, relevance profiles, and article corpora live in
separate private *corpus repositories* that this repository never contains
and never assumes the location of. Anything that names a real feed set, a
real organization, or a real messaging destination belongs in a corpus
repository, not here — `examples/` carries placeholders.

## Invariants

These are load-bearing. Changing one is a design decision, not an edit.

- **`id = sha1(canonical_key)`.** Altering `canonical_key` severs every past
  article from the seen index and from every story that references it. It
  requires a `schema_version` bump and a rebuild procedure.
- **`canonical_key` is a key, not a locator.** It is scheme-less and must
  never be fetched; `url` holds the address the feed gave. Keeping the two
  apart is what allows the key to discard everything that does not
  distinguish one article from another.
- **`origin` is written once.** Editing a corpus's `sources.toml` never
  rewrites records collected earlier. That is what makes the corpus traceable.
- **Nothing is discarded.** Prefiltered and overflowed articles are stored
  with their verdict. The record of why an article did not need reading is
  half of what the corpus is for.
- **Priority is derived, never written by the agent.** The agent scores axes;
  `apply_table.py` applies the profile's decision table. Do not add a priority
  field to the agent's output "for convenience".
- **Merge is idempotent.** The same input applied twice produces no
  difference. Breaking this makes a failed run unrecoverable.
- **Axis IDs are shared vocabulary across profiles.** A profile may sharpen
  what an axis means; repurposing an existing ID makes the accumulated corpus
  incomparable with itself. Adding an axis requires a `profile_version` bump.
- **Standard library only.** "It needs no infrastructure" is the reason this
  tool exists. A dependency costs more than the code it saves.

## Where things belong

Judgement lives in exactly two places: scoring the axes, and summarizing the
must-read articles. Everything else — statistics, stale-topic derivation,
ordering, layout, splitting — is a script. When the output reads wrong, fix
`compile.py`; do not have the model re-render it.

## Untrusted input

Feeds and article bodies are third-party data. `prefilter.py` wraps article
text in nonce-delimited tags before the agent sees it; the XML parser rejects
documents carrying a DTD and caps body size. These are the boundary. Do not
replace them with prose instructions telling the agent what not to do — an
enumeration of prohibited actions parks attacker-shaped strings in the
trusted region and raises the salience of what it names.

## Versions

`config_version` and `schema_version` in a corpus's `.newsrc.toml` are how a
public engine avoids breaking private data it cannot see. `check_config.py`
refuses to run outside the supported range. Widening that range without a
migration path is how the guarantee gets lost.

## Commits

Small and typed: `feat:` / `fix:` / `docs:` / `test:` / `chore:`.

## Language

Japanese for conversation. English for committed files, with `README.ja.md`
and `docs/ja/` kept in sync with their English counterparts.
