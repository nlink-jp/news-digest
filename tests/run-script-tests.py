#!/usr/bin/env python3
"""Behaviour tests for the bundled scripts (stdlib only).

Run from the Makefile `check` target, alongside the vendored structural
validator. The vendored validator is never edited; repo-specific tests live
here.
"""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
SKILL = REPO / "news-digest"
SCRIPTS = SKILL / "scripts"
PROFILES = SKILL / "profiles"

sys.path.insert(0, str(SCRIPTS))

from lib import corpus, profile, records  # noqa: E402


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


if __name__ == "__main__":
    unittest.main(verbosity=2)
