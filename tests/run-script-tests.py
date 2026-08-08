#!/usr/bin/env python3
"""Behaviour tests for the bundled scripts (stdlib only).

Run from the Makefile `check` target, alongside the vendored structural
validator. The vendored validator is never edited; repo-specific tests live
here.
"""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
SKILL = REPO / "news-digest"
SCRIPTS = SKILL / "scripts"
PROFILES = SKILL / "profiles"
FEEDS = REPO / "tests" / "fixtures" / "feeds"

sys.path.insert(0, str(SCRIPTS))

import collect  # noqa: E402
import collectors  # noqa: E402
from lib import corpus, http, profile, records  # noqa: E402
from lib import sources as sources_lib  # noqa: E402
from lib import state as state_lib  # noqa: E402
from lib import window as window_lib  # noqa: E402


def feed(name: str) -> bytes:
    return (FEEDS / name).read_bytes()


# ────────────────────────────────────────────────────────────────
# Identity and text normalization
# ────────────────────────────────────────────────────────────────


class TestCanonicalKey(unittest.TestCase):
    """The key is a comparison form, not a locator — the record's `url` field
    is what gets fetched. That is what lets the key discard the scheme."""

    def test_is_scheme_less(self):
        self.assertEqual(
            records.canonical_key("https://example.com/a?id=7"), "example.com/a?id=7"
        )

    def test_http_and_https_are_one_article(self):
        """Otherwise a site migrating to HTTPS republishes its whole archive
        into a single day's digest."""
        self.assertEqual(
            records.canonical_key("http://example.com/post/1"),
            records.canonical_key("https://example.com/post/1"),
        )

    def test_strips_tracking_parameters_but_keeps_real_ones(self):
        self.assertEqual(
            records.canonical_key("https://example.com/a?id=7&utm_source=rss&fbclid=x"),
            "example.com/a?id=7",
        )

    def test_lowercases_the_host_but_not_the_path(self):
        self.assertEqual(
            records.canonical_key("HTTPS://Example.COM/Path/To/Article"),
            "example.com/Path/To/Article",
        )

    def test_drops_www_and_fragment_and_trailing_slash(self):
        self.assertEqual(
            records.canonical_key("https://www.example.com/a/#section"), "example.com/a"
        )

    def test_drops_a_default_port_but_keeps_a_real_one(self):
        self.assertEqual(records.canonical_key("https://example.com:443/a"), "example.com/a")
        self.assertEqual(records.canonical_key("http://example.com:80/a"), "example.com/a")
        self.assertEqual(records.canonical_key("https://example.com:8443/a"), "example.com:8443/a")

    def test_query_parameter_order_does_not_change_identity(self):
        self.assertEqual(
            records.canonical_key("https://example.com/a?b=2&a=1"),
            records.canonical_key("https://example.com/a?a=1&b=2"),
        )

    def test_root_path_keeps_its_slash(self):
        self.assertEqual(records.canonical_key("https://example.com"), "example.com/")

    def test_variants_of_one_article_collapse_to_one_id(self):
        variants = [
            "https://www.example.com/post/1?utm_campaign=daily",
            "http://example.com/post/1/",
            "https://example.com/post/1#comments",
            "https://example.com/post/1?ref=twitter",
            "HTTPS://WWW.Example.com:443/post/1",
        ]
        ids = {records.article_id(records.canonical_key(u)) for u in variants}
        self.assertEqual(len(ids), 1)

    def test_different_articles_stay_distinct(self):
        distinct = [
            "https://example.com/post/1",
            "https://example.com/post/2",
            "https://example.com/Post/1",  # path case is significant
            "https://other.example.com/post/1",
            "https://example.com/post/1?page=2",
        ]
        keys = {records.canonical_key(u) for u in distinct}
        self.assertEqual(len(keys), len(distinct))

    def test_empty_and_malformed_input_do_not_raise(self):
        self.assertEqual(records.canonical_key(""), "")
        self.assertEqual(records.canonical_key("   "), "")
        self.assertIsInstance(records.canonical_key("not a url"), str)

    def test_a_relative_reference_stands_as_its_own_key(self):
        self.assertEqual(records.canonical_key("/post/1"), "/post/1")


class TestArticleID(unittest.TestCase):
    def test_shape_is_stable_and_self_describing(self):
        got = records.article_id("https://example.com/a")
        self.assertTrue(got.startswith("sha1:"))
        self.assertEqual(len(got), len("sha1:") + 16)

    def test_deterministic(self):
        self.assertEqual(
            records.article_id("https://example.com/a"),
            records.article_id("https://example.com/a"),
        )

    def test_distinct_urls_differ(self):
        self.assertNotEqual(
            records.article_id("https://example.com/a"),
            records.article_id("https://example.com/b"),
        )


class TestTextNormalization(unittest.TestCase):
    def test_normalize_title_drops_outlet_suffix(self):
        self.assertEqual(
            records.normalize_title("重大な脆弱性が公表された | 気になる、記になる…"),
            records.normalize_title("重大な脆弱性が公表された"),
        )

    def test_normalize_title_drops_leading_bracket_label(self):
        self.assertEqual(
            records.normalize_title("【速報】サービス停止"),
            records.normalize_title("サービス停止"),
        )

    def test_strip_html_removes_markup_and_scripts(self):
        got = records.strip_html("<p>Hello <b>world</b></p><script>evil()</script>")
        self.assertNotIn("<", got)
        self.assertNotIn("evil", got)
        self.assertIn("Hello", got)

    def test_strip_html_unescapes_entities(self):
        self.assertEqual(records.strip_html("A &amp; B"), "A & B")

    def test_strip_html_caps_length(self):
        got = records.strip_html("x" * 5000, limit=100)
        self.assertLessEqual(len(got), 101)  # 100 plus the ellipsis

    def test_derive_title_builds_a_headline_for_untitled_entries(self):
        body = "A widely deployed appliance is exploitable before authentication. Details follow."
        got = records.derive_title(body)
        self.assertTrue(got)
        self.assertLessEqual(len(got), 90)

    def test_derive_title_of_empty_body_is_empty(self):
        self.assertEqual(records.derive_title(""), "")


class TestParseDate(unittest.TestCase):
    def test_rfc822_from_rss(self):
        dt = records.parse_date("Fri, 08 Aug 2026 09:30:00 +0900")
        self.assertIsNotNone(dt)
        self.assertEqual(dt.utcoffset().total_seconds(), 9 * 3600)

    def test_rfc3339_from_atom(self):
        dt = records.parse_date("2026-08-08T00:30:00Z")
        self.assertIsNotNone(dt)
        self.assertEqual(dt.utcoffset().total_seconds(), 0)

    def test_bare_date_from_dublin_core(self):
        self.assertIsNotNone(records.parse_date("2026-08-08"))

    def test_naive_timestamp_is_read_as_utc_not_local(self):
        dt = records.parse_date("2026-08-08T00:30:00")
        self.assertIsNotNone(dt)
        self.assertEqual(dt.utcoffset().total_seconds(), 0)

    def test_unparseable_returns_none_rather_than_now(self):
        self.assertIsNone(records.parse_date("last Tuesday"))
        self.assertIsNone(records.parse_date(""))


# ────────────────────────────────────────────────────────────────
# The corpus contract
# ────────────────────────────────────────────────────────────────


NEWSRC = """
[repo]
config_version = 1
schema_version = 1
profile = "generic"

[paths]
config = "config"
data = "data"
digests = "digests"

[[notify]]
id = "main"
kind = "slack"
channel = "C0XXXXXXXXX"
thread = true
"""


class TestCorpusContract(unittest.TestCase):
    def _corpus(self, text: str) -> Path:
        tmp = Path(tempfile.mkdtemp())
        (tmp / corpus.MARKER).write_text(text, encoding="utf-8")
        return tmp

    def test_loads_a_well_formed_corpus(self):
        c = corpus.load(self._corpus(NEWSRC))
        self.assertEqual(c.profile_name, "generic")
        self.assertEqual(c.digests_dir.name, "digests")
        self.assertEqual(len(c.destinations), 1)
        self.assertTrue(c.destinations[0].thread)

    def test_derived_paths_stay_inside_the_corpus(self):
        c = corpus.load(self._corpus(NEWSRC))
        for path in (c.sources_file, c.articles_dir, c.state_file, c.seen_file(2026)):
            self.assertTrue(str(path).startswith(str(c.root)))

    def test_article_file_partitions_by_published_date(self):
        c = corpus.load(self._corpus(NEWSRC))
        self.assertEqual(
            c.article_file("2026-08-08"), c.articles_dir / "2026" / "08" / "08.jsonl"
        )

    def test_missing_marker_is_refused_rather_than_guessed(self):
        with self.assertRaises(corpus.CorpusError):
            corpus.load(Path(tempfile.mkdtemp()))

    def test_unsupported_config_version_stops_the_run(self):
        text = NEWSRC.replace("config_version = 1", "config_version = 99")
        with self.assertRaises(corpus.CorpusError) as ctx:
            corpus.load(self._corpus(text))
        self.assertIn("99", str(ctx.exception))

    def test_unsupported_schema_version_stops_the_run(self):
        text = NEWSRC.replace("schema_version = 1", "schema_version = 99")
        with self.assertRaises(corpus.CorpusError):
            corpus.load(self._corpus(text))

    def test_escaping_path_is_refused(self):
        text = NEWSRC.replace('data = "data"', 'data = "../elsewhere"')
        with self.assertRaises(corpus.CorpusError):
            corpus.load(self._corpus(text))

    def test_absolute_path_is_refused(self):
        text = NEWSRC.replace('data = "data"', 'data = "/tmp/elsewhere"')
        with self.assertRaises(corpus.CorpusError):
            corpus.load(self._corpus(text))

    def test_destination_without_channel_is_refused(self):
        text = NEWSRC.replace('channel = "C0XXXXXXXXX"', 'channel = ""')
        with self.assertRaises(corpus.CorpusError):
            corpus.load(self._corpus(text))

    def test_duplicate_destination_ids_are_refused(self):
        text = NEWSRC + '\n[[notify]]\nid = "main"\nkind = "slack"\nchannel = "C0YYYYYYYYY"\n'
        with self.assertRaises(corpus.CorpusError):
            corpus.load(self._corpus(text))

    def test_invalid_toml_names_the_file(self):
        with self.assertRaises(corpus.CorpusError) as ctx:
            corpus.load(self._corpus("[repo\n"))
        self.assertIn(corpus.MARKER, str(ctx.exception))

    def test_discover_does_not_walk_upwards(self):
        root = self._corpus(NEWSRC)
        child = root / "config"
        child.mkdir()
        with self.assertRaises(corpus.CorpusError):
            corpus.discover(child)


# ────────────────────────────────────────────────────────────────
# Profiles
# ────────────────────────────────────────────────────────────────


class TestShippedProfilesLoad(unittest.TestCase):
    def test_every_shipped_profile_loads(self):
        names = profile.available(PROFILES)
        self.assertIn("generic", names)
        self.assertIn("security-news", names)
        for name in names:
            with self.subTest(profile=name):
                profile.load(PROFILES, name)

    def test_base_profile_axes(self):
        p = profile.load(PROFILES, "generic")
        self.assertEqual(p.axis_ids, ("novelty", "significance", "relevance"))
        for axis in p.axes:
            self.assertEqual((axis.min, axis.max), (0, 3))
            self.assertEqual(set(axis.labels), {0, 1, 2, 3})

    def test_specialization_inherits_the_axis_vocabulary(self):
        base = profile.load(PROFILES, "generic")
        special = profile.load(PROFILES, "security-news")
        self.assertEqual(base.axis_ids, special.axis_ids)
        self.assertEqual(base.priorities, special.priorities)
        self.assertEqual(special.lineage, ("generic", "security-news"))

    def test_specialization_overrides_only_what_it_ships(self):
        special = profile.load(PROFILES, "security-news")
        # rubric and table come from the child, layout from the parent.
        self.assertEqual(special.rubric_path.parent.name, "security-news")
        self.assertIn("security news", special.rubric().lower())
        self.assertEqual(special.layout.deep_read_priorities, ("must_read",))

    def test_unknown_profile_lists_what_is_available(self):
        with self.assertRaises(profile.ProfileError) as ctx:
            profile.load(PROFILES, "no-such-profile")
        self.assertIn("generic", str(ctx.exception))


class TestDecisionTableExhaustive(unittest.TestCase):
    """Every reachable score combination, for every shipped profile.

    This is the test that matters most: the decision table is the only thing
    standing between an axis score and a reading priority, and a combination
    that falls through to an unintended verdict is invisible in production —
    the digest simply omits an article nobody knows to look for.
    """

    TIERS = ("primary", "secondary", "analysis", "community")

    def _combinations(self, p: profile.Profile):
        ranges = [range(a.min, a.max + 1) for a in p.axes]
        ids = p.axis_ids

        def walk(i, acc):
            if i == len(ids):
                for tier in self.TIERS:
                    yield {**acc, "credibility": tier}
                return
            for value in ranges[i]:
                yield from walk(i + 1, {**acc, ids[i]: value})

        yield from walk(0, {})

    def test_every_combination_yields_a_declared_priority(self):
        for name in profile.available(PROFILES):
            p = profile.load(PROFILES, name)
            count = 0
            for values in self._combinations(p):
                priority, _ = p.table.decide(values)
                self.assertIn(priority, p.priorities, f"{name}: {values} -> {priority}")
                count += 1
            self.assertEqual(count, 4 ** len(p.axes) * len(self.TIERS))

    def test_a_rehash_is_never_promoted_however_large(self):
        for name in profile.available(PROFILES):
            p = profile.load(PROFILES, name)
            for values in self._combinations(p):
                if values["novelty"] != 0:
                    continue
                priority, _ = p.table.decide(values)
                self.assertEqual(
                    priority, "minor_update", f"{name}: novelty 0 became {priority} for {values}"
                )

    def test_no_contact_and_no_significance_is_archived(self):
        for name in profile.available(PROFILES):
            p = profile.load(PROFILES, name)
            for novelty in (1, 2, 3):
                priority, _ = p.table.decide(
                    {"novelty": novelty, "significance": 0, "relevance": 0, "credibility": "primary"}
                )
                self.assertEqual(priority, "archive", f"{name}: novelty {novelty}")

    def test_direct_hit_with_a_real_event_is_must_read(self):
        for name in profile.available(PROFILES):
            p = profile.load(PROFILES, name)
            priority, _ = p.table.decide(
                {"novelty": 3, "significance": 2, "relevance": 3, "credibility": "secondary"}
            )
            self.assertEqual(priority, "must_read", name)

    def test_security_profile_discounts_low_credibility_for_the_world_path(self):
        p = profile.load(PROFILES, "security-news")
        base = {"novelty": 3, "significance": 3, "relevance": 2}
        self.assertEqual(p.table.decide({**base, "credibility": "primary"})[0], "must_read")
        self.assertEqual(p.table.decide({**base, "credibility": "analysis"})[0], "must_read")
        self.assertEqual(p.table.decide({**base, "credibility": "community"})[0], "should_read")
        self.assertEqual(p.table.decide({**base, "credibility": "secondary"})[0], "should_read")

    def test_credibility_never_downgrades_a_direct_hit(self):
        """Relevance 3 is about the reader's own environment, which a weak
        source does not make less true."""
        p = profile.load(PROFILES, "security-news")
        for tier in self.TIERS:
            priority, _ = p.table.decide(
                {"novelty": 2, "significance": 2, "relevance": 3, "credibility": tier}
            )
            self.assertEqual(priority, "must_read", tier)

    def test_first_matching_rule_wins(self):
        table = profile.DecisionTable(
            rules=(
                profile.Rule(when={"novelty": 0}, priority="minor_update"),
                profile.Rule(when={"significance": ">=3"}, priority="must_read"),
            ),
            default="archive",
        )
        priority, index = table.decide({"novelty": 0, "significance": 3})
        self.assertEqual((priority, index), ("minor_update", 0))


class TestConditionGrammar(unittest.TestCase):
    def test_comparison_operators(self):
        cases = [(">=2", 2, True), (">=2", 1, False), ("<=1", 1, True), (">0", 0, False),
                 ("<3", 2, True), ("==2", 2, True), ("!=2", 2, False)]
        for test, value, expected in cases:
            with self.subTest(test=test, value=value):
                rule = profile.Rule(when={"x": test}, priority="p")
                self.assertEqual(rule.matches({"x": value}), expected)

    def test_bare_integer_is_equality(self):
        rule = profile.Rule(when={"x": 2}, priority="p")
        self.assertTrue(rule.matches({"x": 2}))
        self.assertFalse(rule.matches({"x": 3}))

    def test_list_is_membership(self):
        rule = profile.Rule(when={"tier": ["primary", "analysis"]}, priority="p")
        self.assertTrue(rule.matches({"tier": "primary"}))
        self.assertFalse(rule.matches({"tier": "community"}))

    def test_all_conditions_must_hold(self):
        rule = profile.Rule(when={"a": ">=2", "b": ">=2"}, priority="p")
        self.assertTrue(rule.matches({"a": 2, "b": 3}))
        self.assertFalse(rule.matches({"a": 2, "b": 1}))

    def test_missing_field_does_not_match(self):
        rule = profile.Rule(when={"a": ">=0"}, priority="p")
        self.assertFalse(rule.matches({}))


class TestProfileValidation(unittest.TestCase):
    """A profile that loads must be a profile that can run — every check here
    is one that would otherwise surface as a broken digest."""

    def _profile_dir(self, files: dict[str, str], name: str = "t") -> Path:
        root = Path(tempfile.mkdtemp())
        directory = root / name
        directory.mkdir()
        for filename, text in files.items():
            (directory / filename).write_text(text, encoding="utf-8")
        return root

    BASE = {
        "profile.toml": 'name = "t"\nprofile_version = 1\npriorities = ["a", "b"]\n',
        "axes.toml": '[[axis]]\nid = "x"\nquestion = "?"\nmin = 0\nmax = 3\n',
        "decision-table.toml": 'default = "b"\n[[rule]]\nwhen = { x = ">=2" }\npriority = "a"\n',
        "layout.toml": 'include_priorities = ["a"]\ndeep_read_priorities = ["a"]\n',
        "rubric.md": "# t\n",
    }

    def test_the_baseline_fixture_loads(self):
        profile.load(self._profile_dir(self.BASE), "t")

    def test_table_referencing_an_unknown_field_is_refused(self):
        files = dict(self.BASE)
        files["decision-table.toml"] = 'default = "b"\n[[rule]]\nwhen = { nope = ">=2" }\npriority = "a"\n'
        with self.assertRaises(profile.ProfileError) as ctx:
            profile.load(self._profile_dir(files), "t")
        self.assertIn("nope", str(ctx.exception))

    def test_table_producing_an_undeclared_priority_is_refused(self):
        files = dict(self.BASE)
        files["decision-table.toml"] = 'default = "b"\n[[rule]]\nwhen = { x = ">=2" }\npriority = "zzz"\n'
        with self.assertRaises(profile.ProfileError):
            profile.load(self._profile_dir(files), "t")

    def test_undeclared_default_priority_is_refused(self):
        files = dict(self.BASE)
        files["decision-table.toml"] = 'default = "zzz"\n'
        with self.assertRaises(profile.ProfileError):
            profile.load(self._profile_dir(files), "t")

    def test_layout_naming_an_undeclared_priority_is_refused(self):
        files = dict(self.BASE)
        files["layout.toml"] = 'include_priorities = ["zzz"]\ndeep_read_priorities = ["a"]\n'
        with self.assertRaises(profile.ProfileError):
            profile.load(self._profile_dir(files), "t")

    def test_a_derived_field_cannot_be_declared_as_an_axis(self):
        files = dict(self.BASE)
        files["axes.toml"] = '[[axis]]\nid = "credibility"\nquestion = "?"\nmin = 0\nmax = 3\n'
        with self.assertRaises(profile.ProfileError) as ctx:
            profile.load(self._profile_dir(files), "t")
        self.assertIn("derived", str(ctx.exception))

    def test_duplicate_axis_id_is_refused(self):
        files = dict(self.BASE)
        files["axes.toml"] = (
            '[[axis]]\nid = "x"\nquestion = "?"\nmin = 0\nmax = 3\n'
            '[[axis]]\nid = "x"\nquestion = "?"\nmin = 0\nmax = 3\n'
        )
        with self.assertRaises(profile.ProfileError):
            profile.load(self._profile_dir(files), "t")

    def test_label_outside_the_axis_range_is_refused(self):
        files = dict(self.BASE)
        files["axes.toml"] = (
            '[[axis]]\nid = "x"\nquestion = "?"\nmin = 0\nmax = 3\n[axis.labels]\n9 = "nope"\n'
        )
        with self.assertRaises(profile.ProfileError):
            profile.load(self._profile_dir(files), "t")

    def test_extends_cycle_is_reported(self):
        root = Path(tempfile.mkdtemp())
        for a, b in (("p", "q"), ("q", "p")):
            directory = root / a
            directory.mkdir()
            (directory / "profile.toml").write_text(
                f'name = "{a}"\nextends = "{b}"\npriorities = ["a"]\n', encoding="utf-8"
            )
        with self.assertRaises(profile.ProfileError) as ctx:
            profile.load(root, "p")
        self.assertIn("cycle", str(ctx.exception))

    def test_axis_score_outside_the_range_is_refused(self):
        p = profile.load(PROFILES, "generic")
        axis = p.axis("novelty")
        self.assertEqual(axis.validate_score(2), 2)
        with self.assertRaises(profile.ProfileError):
            axis.validate_score(4)
        with self.assertRaises(profile.ProfileError):
            axis.validate_score("2")
        with self.assertRaises(profile.ProfileError):
            axis.validate_score(True)


# ────────────────────────────────────────────────────────────────
# Collectors
# ────────────────────────────────────────────────────────────────


class TestCollectorRegistry(unittest.TestCase):
    def test_default_type_is_rss(self):
        self.assertIs(collectors.get(None), collectors.get("rss"))
        self.assertIs(collectors.get(""), collectors.get("rss"))

    def test_unknown_type_is_an_error_not_a_fallback(self):
        """A typo that silently parsed as RSS would yield zero entries and
        look exactly like a quiet feed."""
        with self.assertRaises(collectors.CollectorError) as ctx:
            collectors.get("rss2")
        self.assertIn("rss", str(ctx.exception))

    def test_every_registered_collector_exposes_parse(self):
        for name in collectors.available():
            self.assertTrue(callable(getattr(collectors.get(name), "parse", None)), name)


class TestRSSCollector(unittest.TestCase):
    def test_rss2(self):
        entries = collectors.get("rss").parse(feed("rss2.xml"))
        self.assertEqual(len(entries), 2)
        first = entries[0]
        self.assertEqual(first.title, "Pre-authentication flaw in Example Gateway")
        self.assertEqual(first.url, "https://example.com/advisory/1?utm_source=rss")
        self.assertNotIn("<", first.summary)
        self.assertIn("unauthenticated", first.summary)
        self.assertEqual(first.published_at.utcoffset().total_seconds(), 9 * 3600)

    def test_rss2_falls_back_to_a_permalink_guid_when_link_is_absent(self):
        entries = collectors.get("rss").parse(feed("rss2.xml"))
        self.assertEqual(entries[1].url, "https://example.com/advisory/2")

    def test_atom_prefers_the_alternate_link_over_others(self):
        entries = collectors.get("rss").parse(feed("atom.xml"))
        self.assertEqual(entries[0].url, "https://example.com/research/9")

    def test_atom_accepts_a_lone_link_without_rel(self):
        entries = collectors.get("rss").parse(feed("atom.xml"))
        self.assertEqual(entries[1].url, "https://example.com/research/10")

    def test_rdf_reads_the_address_from_rdf_about_and_the_date_from_dublin_core(self):
        entries = collectors.get("rss").parse(feed("rdf.xml"))
        self.assertEqual(len(entries), 1)
        self.assertEqual(entries[0].url, "https://example.jp/news/100")
        self.assertEqual(entries[0].published_at.utcoffset().total_seconds(), 9 * 3600)

    def test_an_untitled_entry_gets_a_derived_headline(self):
        """Microblog-shaped feeds publish untitled entries. Without this every
        one of them is dropped later by the minimum-title-length rule."""
        entries = collectors.get("rss").parse(feed("untitled.xml"))
        self.assertEqual(len(entries), 1)
        self.assertTrue(entries[0].title)
        self.assertGreaterEqual(len(entries[0].title), 8)

    def test_a_dtd_is_refused_before_parsing(self):
        with self.assertRaises(collectors.CollectorError) as ctx:
            collectors.get("rss").parse(feed("billion-laughs.xml"))
        self.assertIn("DTD", str(ctx.exception))

    def test_an_entity_declaration_is_refused_wherever_it_appears(self):
        body = b"<rss><channel></channel></rss>" + b" " * 70000 + b"<!ENTITY x 'y'>"
        with self.assertRaises(collectors.CollectorError):
            collectors.get("rss").parse(body)

    def test_malformed_xml_raises_rather_than_returning_nothing(self):
        with self.assertRaises(collectors.CollectorError):
            collectors.get("rss").parse(b"<rss><channel><item></rss>")

    def test_an_html_page_is_not_mistaken_for_a_feed(self):
        with self.assertRaises(collectors.CollectorError):
            collectors.get("rss").parse(b"<html><body><p>Not a feed</p></body></html>")

    def test_empty_document_raises(self):
        with self.assertRaises(collectors.CollectorError):
            collectors.get("rss").parse(b"")

    def test_a_byte_order_mark_does_not_break_parsing(self):
        body = "﻿".encode("utf-8") + feed("rss2.xml")
        self.assertEqual(len(collectors.get("rss").parse(body)), 2)


class TestJSONFeedCollector(unittest.TestCase):
    def test_parses_items_and_strips_markup(self):
        entries = collectors.get("jsonfeed").parse(feed("jsonfeed.json"))
        self.assertEqual(len(entries), 2)  # the third has no address
        self.assertEqual(entries[0].url, "https://example.com/j/1")
        self.assertNotIn("<", entries[0].summary)

    def test_id_is_used_as_the_address_when_it_is_one(self):
        entries = collectors.get("jsonfeed").parse(feed("jsonfeed.json"))
        self.assertEqual(entries[1].url, "https://example.com/j/2")

    def test_invalid_json_raises(self):
        with self.assertRaises(collectors.CollectorError):
            collectors.get("jsonfeed").parse(b"{not json")

    def test_a_document_without_items_raises(self):
        with self.assertRaises(collectors.CollectorError) as ctx:
            collectors.get("jsonfeed").parse(b'{"version": "x", "title": "y"}')
        self.assertIn("items", str(ctx.exception))


class TestEntryNormalization(unittest.TestCase):
    def test_an_entry_without_an_address_is_dropped(self):
        self.assertIsNone(collectors.make_entry(url="", title="Has a title"))
        self.assertIsNone(collectors.make_entry(url="   "))

    def test_summary_markup_is_stripped_the_same_way_for_every_collector(self):
        rss_entries = collectors.get("rss").parse(feed("rss2.xml"))
        json_entries = collectors.get("jsonfeed").parse(feed("jsonfeed.json"))
        for entry in rss_entries + json_entries:
            self.assertNotIn("<", entry.summary)
            self.assertNotIn("&lt;", entry.summary)

    def test_an_unparseable_date_leaves_published_at_unset(self):
        entry = collectors.make_entry(url="https://example.com/a", published_raw="soon")
        self.assertIsNone(entry.published_at)


# ────────────────────────────────────────────────────────────────
# The HTTP layer
# ────────────────────────────────────────────────────────────────


class FakeHeaders(dict):
    def get(self, key, default=None):  # case-insensitive, like http.client
        for k, v in self.items():
            if k.lower() == key.lower():
                return v
        return default


class FakeResponse:
    def __init__(self, body: bytes, status: int = 200, headers: dict | None = None, url: str = "https://x/f"):
        self._body = body
        self.status = status
        self.headers = FakeHeaders(headers or {})
        self._url = url

    def read(self, n: int = -1) -> bytes:
        return self._body if n < 0 else self._body[:n]

    def geturl(self) -> str:
        return self._url

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class FakeOpener:
    """Records requests and replays a scripted sequence of outcomes."""

    def __init__(self, *outcomes):
        self.outcomes = list(outcomes)
        self.requests = []

    def open(self, request, timeout=None):
        self.requests.append(request)
        outcome = self.outcomes.pop(0) if self.outcomes else FakeResponse(b"ok")
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


def http_error(code: int, headers: dict | None = None):
    import urllib.error
    import email.message

    msg = email.message.Message()
    for k, v in (headers or {}).items():
        msg[k] = v
    return urllib.error.HTTPError("https://x/f", code, "err", msg, None)


class TestHttpClient(unittest.TestCase):
    def _client(self, *outcomes, **kw):
        slept: list[float] = []
        opener = FakeOpener(*outcomes)
        client = http.HttpClient(
            opener=opener, sleep=slept.append, backoff_base=1.0, **kw
        )
        return client, opener, slept

    def test_sends_the_configured_user_agent(self):
        client, opener, _ = self._client(FakeResponse(b"body"))
        client.get("https://example.com/f")
        self.assertEqual(opener.requests[0].get_header("User-agent"), http.DEFAULT_USER_AGENT)

    def test_sends_validators_when_the_caller_holds_them(self):
        client, opener, _ = self._client(FakeResponse(b"body"))
        client.get("https://example.com/f", etag='W/"abc"', last_modified="Thu, 07 Aug 2026 00:00:00 GMT")
        request = opener.requests[0]
        self.assertEqual(request.get_header("If-none-match"), 'W/"abc"')
        self.assertEqual(request.get_header("If-modified-since"), "Thu, 07 Aug 2026 00:00:00 GMT")

    def test_304_is_a_result_not_an_error(self):
        """Unchanged is a normal outcome. Reporting it as an empty feed would
        make every quiet source look broken."""
        client, _, _ = self._client(http_error(304))
        response = client.get("https://example.com/f")
        self.assertTrue(response.not_modified)
        self.assertEqual(response.body, b"")

    def test_returns_validators_for_the_next_run(self):
        client, _, _ = self._client(
            FakeResponse(b"body", headers={"ETag": '"v2"', "Last-Modified": "Fri, 08 Aug 2026 00:00:00 GMT"})
        )
        response = client.get("https://example.com/f")
        self.assertEqual(response.etag, '"v2"')
        self.assertEqual(response.last_modified, "Fri, 08 Aug 2026 00:00:00 GMT")

    def test_decompresses_a_gzip_body(self):
        import gzip as gz

        payload = b"<rss/>" * 100
        client, _, _ = self._client(
            FakeResponse(gz.compress(payload), headers={"Content-Encoding": "gzip"})
        )
        self.assertEqual(client.get("https://example.com/f").body, payload)

    def test_a_body_over_the_cap_is_refused_not_truncated(self):
        """A truncated feed parses into a plausible but wrong set of articles,
        which is worse than no feed at all."""
        client, _, _ = self._client(FakeResponse(b"x" * 5000), max_bytes=1000)
        with self.assertRaises(http.HttpError) as ctx:
            client.get("https://example.com/f")
        self.assertEqual(ctx.exception.kind, "too_large")

    def test_a_body_exactly_at_the_cap_is_accepted(self):
        client, _, _ = self._client(FakeResponse(b"x" * 1000), max_bytes=1000)
        self.assertEqual(len(client.get("https://example.com/f").body), 1000)

    def test_retries_a_transient_status_then_succeeds(self):
        client, opener, slept = self._client(http_error(503), FakeResponse(b"ok"))
        self.assertEqual(client.get("https://example.com/f").body, b"ok")
        self.assertEqual(len(opener.requests), 2)
        self.assertEqual(slept, [1.0])

    def test_does_not_retry_an_answered_error(self):
        client, opener, _ = self._client(http_error(404), FakeResponse(b"ok"))
        with self.assertRaises(http.HttpError) as ctx:
            client.get("https://example.com/f")
        self.assertEqual(ctx.exception.status, 404)
        self.assertEqual(len(opener.requests), 1)

    def test_obeys_retry_after_on_429(self):
        """Ignoring a stated delay is how a polite client becomes the reason a
        feed blocks it."""
        client, _, slept = self._client(http_error(429, {"Retry-After": "7"}), FakeResponse(b"ok"))
        client.get("https://example.com/f")
        self.assertEqual(slept, [7.0])

    def test_caps_an_absurd_retry_after(self):
        client, _, slept = self._client(http_error(429, {"Retry-After": "99999"}), FakeResponse(b"ok"))
        client.get("https://example.com/f")
        self.assertEqual(slept, [120.0])

    def test_backoff_grows(self):
        client, _, slept = self._client(
            http_error(503), http_error(503), FakeResponse(b"ok"), retries=2
        )
        client.get("https://example.com/f")
        self.assertEqual(slept, [1.0, 2.0])

    def test_gives_up_after_the_retry_budget(self):
        client, opener, _ = self._client(
            http_error(503), http_error(503), http_error(503), retries=2
        )
        with self.assertRaises(http.HttpError):
            client.get("https://example.com/f")
        self.assertEqual(len(opener.requests), 3)

    def test_a_timeout_is_classified_and_retried(self):
        import urllib.error

        client, opener, _ = self._client(
            urllib.error.URLError("timed out"), FakeResponse(b"ok")
        )
        client.get("https://example.com/f")
        self.assertEqual(len(opener.requests), 2)

    def test_refuses_a_non_http_url(self):
        client, opener, _ = self._client()
        for url in ("file:///etc/passwd", "ftp://example.com/f", "/relative"):
            with self.subTest(url=url):
                with self.assertRaises(http.HttpError) as ctx:
                    client.get(url)
                self.assertEqual(ctx.exception.kind, "bad_scheme")
        self.assertEqual(opener.requests, [])

    def test_error_kinds_are_stable_identifiers(self):
        import urllib.error

        cases = [
            (http_error(500), "http_status"),
            (urllib.error.URLError("timed out"), "timeout"),
            (urllib.error.URLError("nodename nor servname provided"), "unreachable"),
        ]
        for outcome, kind in cases:
            with self.subTest(kind=kind):
                client, _, _ = self._client(outcome, retries=0)
                with self.assertRaises(http.HttpError) as ctx:
                    client.get("https://example.com/f")
                self.assertEqual(ctx.exception.kind, kind)


# ────────────────────────────────────────────────────────────────
# Source list
# ────────────────────────────────────────────────────────────────


SOURCE = """
[[source]]
id = "example"
name = "Example"
url = "https://example.com/feed"
category = "security"
lang = "en"
tier = "primary"
"""

TYPES = ("rss", "jsonfeed")


class TestSourceList(unittest.TestCase):
    def _load(self, text: str):
        tmp = Path(tempfile.mkdtemp()) / "sources.toml"
        tmp.write_text(text, encoding="utf-8")
        return sources_lib.load(tmp, TYPES)

    def test_minimal_source_loads_with_defaults(self):
        source = self._load(SOURCE)[0]
        self.assertEqual(source.type, "rss")
        self.assertEqual(source.weight, 1.0)
        self.assertTrue(source.enabled)

    def test_origin_is_a_copy_taken_at_collection_time(self):
        source = self._load(SOURCE)[0]
        origin = source.origin()
        origin["source_name"] = "mutated"
        self.assertEqual(source.origin()["source_name"], "Example")

    def test_unknown_type_is_refused(self):
        with self.assertRaises(sources_lib.SourceError):
            self._load(SOURCE + 'type = "sitemap"\n')

    def test_unknown_tier_is_refused(self):
        with self.assertRaises(sources_lib.SourceError) as ctx:
            self._load(SOURCE.replace('tier = "primary"', 'tier = "blog"'))
        self.assertIn("primary", str(ctx.exception))

    def test_non_http_url_is_refused(self):
        with self.assertRaises(sources_lib.SourceError):
            self._load(SOURCE.replace("https://example.com/feed", "file:///etc/passwd"))

    def test_bad_id_is_refused(self):
        with self.assertRaises(sources_lib.SourceError):
            self._load(SOURCE.replace('id = "example"', 'id = "Example Feed"'))

    def test_duplicate_ids_are_refused(self):
        with self.assertRaises(sources_lib.SourceError):
            self._load(SOURCE + SOURCE)

    def test_a_typo_in_a_key_is_refused_rather_than_ignored(self):
        """A silently ignored `weght = 2.0` is a setting the operator believes
        is in effect."""
        with self.assertRaises(sources_lib.SourceError) as ctx:
            self._load(SOURCE + "weght = 2.0\n")
        self.assertIn("weght", str(ctx.exception))

    def test_zero_weight_is_refused_in_favour_of_disabling(self):
        with self.assertRaises(sources_lib.SourceError):
            self._load(SOURCE + "weight = 0\n")

    def test_a_literal_token_is_refused(self):
        text = SOURCE + '\n[source.auth]\ntoken_env = "X"\ntoken = "secret"\n'
        with self.assertRaises(sources_lib.SourceError) as ctx:
            self._load(text)
        self.assertIn("environment variable", str(ctx.exception))

    def test_auth_builds_a_header_from_the_environment(self):
        import os

        text = SOURCE + '\n[source.auth]\ntoken_env = "ND_TEST_TOKEN"\n'
        source = self._load(text)[0]
        os.environ["ND_TEST_TOKEN"] = "abc123"
        try:
            self.assertEqual(source.auth.resolve(), {"Authorization": "Bearer abc123"})
        finally:
            del os.environ["ND_TEST_TOKEN"]

    def test_auth_without_the_environment_variable_fails_loudly(self):
        text = SOURCE + '\n[source.auth]\ntoken_env = "ND_ABSENT_TOKEN"\n'
        source = self._load(text)[0]
        with self.assertRaises(sources_lib.SourceError):
            source.auth.resolve()

    def test_select_defaults_to_the_enabled_sources(self):
        text = SOURCE + SOURCE.replace('id = "example"', 'id = "off"') + "enabled = false\n"
        chosen, skipped = sources_lib.select(self._load(text), None)
        self.assertEqual([s.id for s in chosen], ["example"])
        self.assertEqual([s.id for s in skipped], ["off"])

    def test_naming_a_disabled_source_overrides_enabled(self):
        """Asking for a source by name and silently collecting nothing would
        be a lie."""
        text = SOURCE + SOURCE.replace('id = "example"', 'id = "off"') + "enabled = false\n"
        chosen, _ = sources_lib.select(self._load(text), ["off"])
        self.assertEqual([s.id for s in chosen], ["off"])

    def test_naming_an_unknown_source_is_refused(self):
        with self.assertRaises(sources_lib.SourceError):
            sources_lib.select(self._load(SOURCE), ["nope"])


# ────────────────────────────────────────────────────────────────
# Window and gap detection
# ────────────────────────────────────────────────────────────────


class TestWindow(unittest.TestCase):
    TZ = timezone(timedelta(hours=9))
    NOW = datetime(2026, 8, 8, 10, 0, tzinfo=TZ)

    def test_default_is_yesterday_midnight_until_now(self):
        win = window_lib.resolve(None, None, tz=self.TZ, now=self.NOW)
        self.assertEqual(win.since, datetime(2026, 8, 7, 0, 0, tzinfo=self.TZ))
        self.assertEqual(win.until, self.NOW)

    def test_a_bare_date_is_midnight_in_the_given_zone(self):
        win = window_lib.resolve("2026-08-01", None, tz=self.TZ, now=self.NOW)
        self.assertEqual(win.since, datetime(2026, 8, 1, 0, 0, tzinfo=self.TZ))

    def test_an_explicit_offset_in_the_input_is_honoured(self):
        win = window_lib.resolve("2026-08-01T00:00:00+00:00", None, tz=self.TZ, now=self.NOW)
        self.assertEqual(win.since.utcoffset().total_seconds(), 0)

    def test_all_means_no_lower_bound(self):
        win = window_lib.resolve("all", None, tz=self.TZ, now=self.NOW)
        self.assertIsNone(win.since)
        self.assertTrue(win.contains(datetime(1999, 1, 1, tzinfo=timezone.utc)))

    def test_window_is_half_open(self):
        win = window_lib.resolve("2026-08-07", "2026-08-08", tz=self.TZ, now=self.NOW)
        self.assertTrue(win.contains(datetime(2026, 8, 7, 0, 0, tzinfo=self.TZ)))
        self.assertFalse(win.contains(datetime(2026, 8, 8, 0, 0, tzinfo=self.TZ)))

    def test_an_undated_article_is_included(self):
        """Feeds omit and mangle dates often enough that excluding them would
        drop real articles silently."""
        win = window_lib.resolve("2026-08-07", None, tz=self.TZ, now=self.NOW)
        self.assertTrue(win.contains(None))

    def test_an_empty_window_is_refused(self):
        with self.assertRaises(window_lib.WindowError):
            window_lib.resolve("2026-08-08", "2026-08-07", tz=self.TZ, now=self.NOW)

    def test_an_unreadable_bound_is_refused(self):
        with self.assertRaises(window_lib.WindowError):
            window_lib.resolve("last Tuesday", None, tz=self.TZ, now=self.NOW)

    def test_gap_is_detected_when_the_window_opens_after_the_last_article_seen(self):
        win = window_lib.resolve("2026-08-07", None, tz=self.TZ, now=self.NOW)
        self.assertTrue(window_lib.gap_before(win, "2026-08-01T00:00:00+09:00"))

    def test_no_gap_when_the_window_overlaps_what_was_seen(self):
        win = window_lib.resolve("2026-08-07", None, tz=self.TZ, now=self.NOW)
        self.assertFalse(window_lib.gap_before(win, "2026-08-07T12:00:00+09:00"))

    def test_no_gap_for_a_source_never_collected(self):
        win = window_lib.resolve("2026-08-07", None, tz=self.TZ, now=self.NOW)
        self.assertFalse(window_lib.gap_before(win, None))


# ────────────────────────────────────────────────────────────────
# Per-source state
# ────────────────────────────────────────────────────────────────


class TestSourceState(unittest.TestCase):
    def test_last_seen_only_moves_forward(self):
        """A feed briefly serving an older page must not rewind the marker
        gap detection depends on."""
        st = state_lib.SourceState()
        st.record_success(fetched_at="t1", newest_published="2026-08-08T00:00:00+00:00")
        st.record_success(fetched_at="t2", newest_published="2026-08-01T00:00:00+00:00")
        self.assertEqual(st.last_seen_published_at, "2026-08-08T00:00:00+00:00")

    def test_success_clears_the_error_streak(self):
        st = state_lib.SourceState(consecutive_errors=3)
        st.record_success(fetched_at="t", newest_published=None)
        self.assertEqual(st.consecutive_errors, 0)

    def test_not_modified_also_clears_the_streak(self):
        st = state_lib.SourceState(consecutive_errors=2)
        st.record_not_modified(fetched_at="t")
        self.assertEqual(st.consecutive_errors, 0)
        self.assertEqual(st.last_status, "not_modified")

    def test_an_error_drops_the_validators(self):
        """Replaying a stale ETag against a resource that moved would keep
        answering 304 forever."""
        st = state_lib.SourceState(etag='"v1"', last_modified="then")
        st.record_error(fetched_at="t", kind="http_status")
        self.assertIsNone(st.etag)
        self.assertIsNone(st.last_modified)

    def test_a_source_is_called_dead_only_after_a_streak(self):
        st = state_lib.SourceState()
        for _ in range(state_lib.DEAD_AFTER - 1):
            st.record_error(fetched_at="t", kind="unreachable")
        self.assertFalse(st.probably_dead)
        st.record_error(fetched_at="t", kind="unreachable")
        self.assertTrue(st.probably_dead)

    def test_round_trips_through_the_file(self):
        path = Path(tempfile.mkdtemp()) / "state" / "sources.json"
        store = state_lib.Store(path)
        store.get("a").record_success(fetched_at="t", newest_published="2026-08-08T00:00:00+00:00")
        store.save()
        again = state_lib.Store.load(path)
        self.assertEqual(again.get("a").last_seen_published_at, "2026-08-08T00:00:00+00:00")

    def test_a_corrupt_state_file_costs_one_refetch_not_the_run(self):
        path = Path(tempfile.mkdtemp()) / "sources.json"
        path.write_text("{not json", encoding="utf-8")
        store = state_lib.Store.load(path)
        self.assertEqual(store.get("a").etag, None)

    def test_unknown_fields_in_a_newer_state_file_are_ignored(self):
        path = Path(tempfile.mkdtemp()) / "sources.json"
        path.write_text('{"a": {"etag": "x", "from_the_future": 1}}', encoding="utf-8")
        self.assertEqual(state_lib.Store.load(path).get("a").etag, "x")


# ────────────────────────────────────────────────────────────────
# Collection
# ────────────────────────────────────────────────────────────────


class TestFetchSource(unittest.TestCase):
    def _source(self, **kw):
        base = dict(
            id="example", name="Example", url="https://example.com/feed",
            type="rss", category="security", lang="en", tier="primary",
        )
        base.update(kw)
        return sources_lib.Source(**base)

    def _client(self, *outcomes):
        return http.HttpClient(opener=FakeOpener(*outcomes), sleep=lambda _: None)

    def test_a_successful_fetch_yields_entries_and_validators(self):
        client = self._client(
            FakeResponse(feed("rss2.xml"), headers={"ETag": '"v1"'})
        )
        result = collect.fetch_source(self._source(), client, state_lib.SourceState())
        self.assertEqual(result.status, "ok")
        self.assertEqual(result.fetched, 2)
        self.assertEqual(result.etag, '"v1"')
        self.assertEqual(result.newest_published, "2026-08-08T09:30:00+09:00")

    def test_stored_validators_are_replayed(self):
        opener = FakeOpener(FakeResponse(feed("rss2.xml")))
        client = http.HttpClient(opener=opener, sleep=lambda _: None)
        st = state_lib.SourceState(etag='"v1"')
        collect.fetch_source(self._source(), client, st)
        self.assertEqual(opener.requests[0].get_header("If-none-match"), '"v1"')

    def test_no_conditional_ignores_stored_validators(self):
        opener = FakeOpener(FakeResponse(feed("rss2.xml")))
        client = http.HttpClient(opener=opener, sleep=lambda _: None)
        st = state_lib.SourceState(etag='"v1"')
        collect.fetch_source(self._source(), client, st, conditional=False)
        self.assertIsNone(opener.requests[0].get_header("If-none-match"))

    def test_304_is_reported_as_unchanged_not_as_empty(self):
        result = collect.fetch_source(
            self._source(), self._client(http_error(304)), state_lib.SourceState()
        )
        self.assertEqual(result.status, "not_modified")
        self.assertEqual(result.fetched, 0)

    def test_a_transport_failure_is_captured_rather_than_raised(self):
        """One failing source must not stop the run."""
        result = collect.fetch_source(
            self._source(), self._client(http_error(500), http_error(500), http_error(500)),
            state_lib.SourceState(),
        )
        self.assertEqual(result.status, "error")
        self.assertEqual(result.error_kind, "http_status")

    def test_an_unparseable_body_is_an_error_not_an_empty_feed(self):
        result = collect.fetch_source(
            self._source(), self._client(FakeResponse(b"<html>nope</html>")),
            state_lib.SourceState(),
        )
        self.assertEqual(result.status, "error")
        self.assertEqual(result.error_kind, "unparseable")

    def test_the_declared_collector_is_used(self):
        result = collect.fetch_source(
            self._source(type="jsonfeed"), self._client(FakeResponse(feed("jsonfeed.json"))),
            state_lib.SourceState(),
        )
        self.assertEqual(result.status, "ok")
        self.assertEqual(result.fetched, 2)

    def test_the_source_language_is_requested(self):
        opener = FakeOpener(FakeResponse(feed("rss2.xml")))
        client = http.HttpClient(opener=opener, sleep=lambda _: None)
        collect.fetch_source(self._source(lang="ja"), client, state_lib.SourceState())
        self.assertIn("ja", opener.requests[0].get_header("Accept-language"))


class TestRecordConstruction(unittest.TestCase):
    def _source(self):
        return sources_lib.Source(
            id="example", name="Example", url="https://example.com/feed",
            type="rss", category="security", lang="en", tier="primary", weight=1.5,
        )

    def test_a_record_carries_its_origin_and_both_addresses(self):
        entry = collectors.get("rss").parse(feed("rss2.xml"))[0]
        record = collect.to_record(entry, self._source(), "2026-08-08T00:00:00+00:00")
        self.assertEqual(record["url"], "https://example.com/advisory/1?utm_source=rss")
        self.assertEqual(record["canonical_key"], "example.com/advisory/1")
        self.assertEqual(record["id"], records.article_id("example.com/advisory/1"))
        self.assertEqual(record["origin"]["tier"], "primary")
        self.assertEqual(record["origin"]["weight"], 1.5)
        self.assertEqual(record["schema_version"], corpus.SCHEMA_WRITE_VERSION)

    def test_the_tracking_parameter_survives_in_the_fetchable_url_only(self):
        """The key is for comparison; the address is what gets fetched."""
        entry = collectors.get("rss").parse(feed("rss2.xml"))[0]
        record = collect.to_record(entry, self._source(), "t")
        self.assertIn("utm_source", record["url"])
        self.assertNotIn("utm_source", record["canonical_key"])


class TestCollectEndToEnd(unittest.TestCase):
    """Drives collect.main() over a temporary corpus with the network faked,
    which is the only thing that proves the wiring."""

    SOURCES = """
[[source]]
id = "alpha"
name = "Alpha"
url = "https://alpha.example/feed"
category = "security"
lang = "en"
tier = "primary"

[[source]]
id = "beta"
name = "Beta"
url = "https://beta.example/feed.json"
type = "jsonfeed"
category = "tech"
lang = "en"
tier = "secondary"

[[source]]
id = "gone"
name = "Gone"
url = "https://gone.example/feed"
category = "tech"
lang = "en"
tier = "community"

[[source]]
id = "off"
name = "Disabled"
url = "https://off.example/feed"
category = "tech"
lang = "en"
tier = "community"
enabled = false
"""

    def setUp(self):
        self.root = Path(tempfile.mkdtemp())
        (self.root / corpus.MARKER).write_text(NEWSRC, encoding="utf-8")
        (self.root / "config").mkdir()
        (self.root / "config" / "sources.toml").write_text(self.SOURCES, encoding="utf-8")
        self.work = self.root / ".work"
        self._real_client = collect.http.HttpClient

    def tearDown(self):
        collect.http.HttpClient = self._real_client

    def _fake_network(self, by_host: dict):
        class Router:
            def open(self, request, timeout=None):
                host = request.host if hasattr(request, "host") else ""
                outcome = by_host.get(host.split(":")[0])
                if outcome is None:
                    raise http_error(404)
                if isinstance(outcome, Exception):
                    raise outcome
                return outcome

        collect.http.HttpClient = lambda *a, **kw: self._real_client(
            *a, opener=Router(), sleep=lambda _: None, retries=0, **kw
        )

    def _run(self, *extra):
        argv = sys.argv
        sys.argv = [
            "collect.py", "--repo", str(self.root),
            "--since", "2026-08-01", "--until", "2026-08-09",
            "--out", str(self.work / "collected.jsonl"),
            "--stats-out", str(self.work / "stats.json"),
            *extra,
        ]
        try:
            return collect.main()
        finally:
            sys.argv = argv

    def _outputs(self):
        lines = (self.work / "collected.jsonl").read_text(encoding="utf-8").splitlines()
        stats = json.loads((self.work / "stats.json").read_text(encoding="utf-8"))
        return [json.loads(line) for line in lines], stats

    def test_collects_across_source_types_and_reports_every_source(self):
        self._fake_network({
            "alpha.example": FakeResponse(feed("rss2.xml"), headers={"ETag": '"a1"'}),
            "beta.example": FakeResponse(feed("jsonfeed.json")),
        })
        self.assertEqual(self._run(), 0)
        articles, stats = self._outputs()

        self.assertEqual(len(articles), 4)  # 2 rss + 2 jsonfeed
        self.assertEqual(stats["totals"]["sources_ok"], 2)
        self.assertEqual(stats["totals"]["sources_error"], 1)
        self.assertEqual(stats["disabled_sources"], ["off"])
        self.assertEqual(stats["errors"], ["gone"])
        # Every selected source appears, whatever happened to it.
        self.assertEqual({s["id"] for s in stats["sources"]}, {"alpha", "beta", "gone"})

    def test_a_failing_source_does_not_stop_the_others(self):
        self._fake_network({"alpha.example": FakeResponse(feed("rss2.xml"))})
        self.assertEqual(self._run(), 0)
        articles, stats = self._outputs()
        self.assertEqual(len(articles), 2)
        self.assertEqual(sorted(stats["errors"]), ["beta", "gone"])

    def test_records_carry_the_origin_of_the_source_that_produced_them(self):
        self._fake_network({
            "alpha.example": FakeResponse(feed("rss2.xml")),
            "beta.example": FakeResponse(feed("jsonfeed.json")),
        })
        self._run()
        articles, _ = self._outputs()
        by_source = {}
        for article in articles:
            by_source.setdefault(article["origin"]["source_id"], []).append(article)
        self.assertEqual(set(by_source), {"alpha", "beta"})
        self.assertTrue(all(a["origin"]["collector"] == "rss" for a in by_source["alpha"]))
        self.assertTrue(all(a["origin"]["tier"] == "secondary" for a in by_source["beta"]))

    def test_state_is_written_and_replayed_on_the_next_run(self):
        self._fake_network({"alpha.example": FakeResponse(feed("rss2.xml"), headers={"ETag": '"a1"'})})
        self._run()
        st = state_lib.Store.load(self.root / "data" / "state" / "sources.json")
        self.assertEqual(st.get("alpha").etag, '"a1"')
        self.assertEqual(st.get("alpha").last_seen_published_at, "2026-08-08T09:30:00+09:00")
        self.assertGreater(st.get("gone").consecutive_errors, 0)

    def test_an_unchanged_source_is_reported_as_unchanged(self):
        self._fake_network({"alpha.example": http_error(304)})
        self._run()
        _, stats = self._outputs()
        alpha = next(s for s in stats["sources"] if s["id"] == "alpha")
        self.assertEqual(alpha["status"], "not_modified")
        self.assertEqual(stats["totals"]["sources_not_modified"], 1)
        self.assertNotIn("alpha", stats["silent_sources"])

    def test_a_source_that_returned_nothing_in_window_is_named(self):
        """Distinct from unchanged: the feed answered and had nothing recent,
        which is worth suspecting."""
        self._fake_network({"alpha.example": FakeResponse(feed("rss2.xml"))})
        argv = sys.argv
        sys.argv = [
            "collect.py", "--repo", str(self.root),
            "--since", "2026-01-01", "--until", "2026-01-02",
            "--out", str(self.work / "collected.jsonl"),
            "--stats-out", str(self.work / "stats.json"),
        ]
        try:
            collect.main()
        finally:
            sys.argv = argv
        _, stats = self._outputs()
        self.assertIn("alpha", stats["silent_sources"])

    def test_a_gap_is_reported_when_the_window_opens_after_what_was_seen(self):
        store = state_lib.Store(self.root / "data" / "state" / "sources.json")
        store.get("alpha").last_seen_published_at = "2026-07-01T00:00:00+00:00"
        store.save()
        self._fake_network({"alpha.example": FakeResponse(feed("rss2.xml"))})
        self._run()
        _, stats = self._outputs()
        self.assertIn("alpha", stats["gaps"])

    def test_selecting_one_source_collects_only_that_one(self):
        self._fake_network({"alpha.example": FakeResponse(feed("rss2.xml"))})
        self.assertEqual(self._run("--source", "alpha"), 0)
        _, stats = self._outputs()
        self.assertEqual([s["id"] for s in stats["sources"]], ["alpha"])

    def test_the_resolved_window_is_recorded(self):
        self._fake_network({"alpha.example": FakeResponse(feed("rss2.xml"))})
        self._run()
        _, stats = self._outputs()
        self.assertTrue(stats["window"]["since"].startswith("2026-08-01"))

    def test_a_corpus_without_a_marker_stops_before_touching_anything(self):
        empty = Path(tempfile.mkdtemp())
        argv = sys.argv
        sys.argv = ["collect.py", "--repo", str(empty)]
        try:
            self.assertEqual(collect.main(), 2)
        finally:
            sys.argv = argv


if __name__ == "__main__":
    unittest.main(verbosity=2)
