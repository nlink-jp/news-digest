# Scheduled operation

A corpus earns its value when the digest arrives without being asked for.
This file states the contract an unattended run relies on, then gives
recipes. Schedule only after a supervised run has produced a digest the user
believes — [setting-up-a-corpus.md](setting-up-a-corpus.md) ends where this
file begins.

## The contract

An unattended run is the same run. The skill distinguishes the two in exactly
one place: interactively it shows the must-read headlines before sending;
unattended it sends without asking (Phase 7). Everything else — stopping and
reporting on broken configuration, anomalies in the digest, the commit — is
identical.

What the scheduled invocation must provide:

- **The skill by name and the corpus by absolute path.** A scheduled run does
  not inherit anyone's working directory, and this skill never guesses which
  corpus to operate on.
- **Network access** to the feeds, and **git credentials** that push to the
  corpus remote non-interactively. A corpus without a remote fails its push
  step on every run; create the remote first.
- **A messaging tool** that reaches the declared destination — for a Slack
  destination, any Slack MCP server offering a send-message tool. If none is
  available, the run completes without sending and says so; if that defeats
  the point of the schedule, fix the tool availability, not the skill.

The prompt for the scheduled task is one line:

```
Run the news-digest skill: /news-digest --repo /absolute/path/to/your-corpus
```

The default window — previous day 00:00 to now — is designed for a daily
schedule: consecutive runs overlap rather than abut, and merge is idempotent,
so an article seen by two runs is recorded once and a gap cannot open between
them. Pick a consistent hour; the digest is dated by the day it runs.

## Recipes

**A scheduled task in the Claude app or Claude Code** — a daily task at the
hour the user reads, with the one-line prompt above. Prefer this route: the
run happens in an environment that already has the skill registered and the
messaging tools connected.

**cron or launchd driving the headless CLI** — a sketch, to adapt:

```
0 8 * * * claude -p "Run the news-digest skill: /news-digest --repo /absolute/path/to/your-corpus"
```

The headless environment must have the skill installed, the messaging tool
configured, and permission settings that let the skill's commands run
unprompted — a crontab entry without them produces digests that go nowhere,
or a run that stalls on its first permission prompt.

## When the digest stops arriving

In order:

1. **The scheduled task's transcript.** A run that stopped on broken
   configuration said so there — that is where stop-and-report goes when
   nobody is watching.
2. **The corpus.** `git -C <corpus> log` — a run that completed committed. A
   commit with no digest delivered means sending failed; check the messaging
   tool's availability in the scheduled environment.
3. **Neither.** Nothing committed, no transcript — the schedule itself did
   not fire, and the problem is outside the skill.

A failed run needs no cleanup. The work directory is cleared at the start of
the next run, and merge is idempotent — re-running is always safe.

## What stays manual

Configuration is edited by a human, on the remote (Phase 0 pulls before
reading it, so an edit made on GitHub reaches the next run without touching
the machine the schedule lives on). The maintenance observations that runs
surface — a stale feed, a source that has never produced a must-read — arrive
in each run's report; acting on them is a decision, not a reflex, and not
something a scheduled run does to its own configuration.
