# Scoring rubric — security news

Read this before scoring. The procedure and the rules that hold regardless of
subject are in the base profile's `rubric.md`; this file says what the axis
scores mean for security coverage. Axis definitions are in `axes.toml`.

## novelty — against the story, not the calendar

Security events generate long tails. The same breach is reported by a dozen
outlets over a week, and most of those reports contain nothing the first one
did not.

- **0** — a re-report, a syndicated copy, a roundup, or a follow-up whose only
  new content is that other people also wrote about it. Also: an advisory
  restating a CVE already in the attached story.
- **1** — one new concrete detail: a version number, a patch date, a confirmed
  victim count, an attribution claim.
- **2** — a development that changes what to do: a patch released, exploitation
  confirmed in the wild, scope revised upward.
- **3** — first report of the matter, or a fact that reframes it (the intrusion
  vector turns out to be different; the "leak" turns out to be recycled data).

## significance — the world, with you removed

Ask what a well-informed practitioner elsewhere would make of it.

- **3** — changes the premises: a widely deployed product is exploitable
  pre-auth, a trusted supply chain is confirmed compromised, an authority
  mandates action, a technique that defeats a standard control is demonstrated.
- **2** — many organizations have to respond or reconsider: a serious
  vulnerability in common software, a campaign against a whole sector, a
  regulatory ruling with precedent value.
- **1** — of interest to people who follow the area: a threat-actor profile, a
  research write-up, a single organization's breach with no transferable lesson.
- **0** — a product announcement, a funding round, a vendor's marketing claim.

Read the evidence, not the adjectives. A "critical" CVSS score on software
nobody runs is not significance 3, and an unauthenticated RCE in something
everyone runs is significance 3 whether or not the headline says so. Proof of
exploitation outweighs theoretical severity.

## relevance — only you

Scored against the reader's `interests.toml`: their platforms, their
languages, the products they run, the products their customers run, and the
obligations they are under.

- **3** — a product in `stack` or `watch_products` is affected; a customer
  environment is implicated; a regulator with authority over the reader has
  acted; the reader's own vendors are involved.
- **2** — not direct, but it is material for an advisory, a proposal, or an
  internal share. Another organization's incident with a transferable lesson
  sits here.
- **1** — background on the field.
- **0** — no point of contact.

**Another organization's breach is material, not a direct hit.** Scoring
domestic incident reports as relevance 3 by reflex makes every one of them a
must_read and empties the category of meaning. Reserve 3 for what actually
lands on the reader.

## Credibility is not yours to score

The decision table consults the source's tier — `primary` (vendor or authority
publishing about itself), `analysis` (first-party research), `secondary`
(reporting), `community` (forums, social). It is derived from the source
list, not judged per article. Do not raise significance to compensate for a
weak source, and do not lower it because you distrust the outlet: score what
the article establishes and let the table apply the discount.

## Indicators

When an article carries indicators — addresses, hashes, payload URLs, leak
sites — they are evidence about the article, and they are recorded defanged
(`203[.]0[.]113[.]45`, `hxxps://…`). None of them is a destination to visit.
