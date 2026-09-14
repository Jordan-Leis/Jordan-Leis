"""Offline checks for aggregation semantics and safe refresh failures."""
from datetime import datetime, timedelta, timezone
from io import BytesIO
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
from urllib.error import HTTPError, URLError
import xml.etree.ElementTree as ET

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import gen_stats as stats
import gen_hero as hero

NOW = datetime(2026, 9, 13, tzinfo=timezone.utc)


def connection(nodes, next_cursor=None):
    return {"nodes": nodes, "pageInfo": {"hasNextPage": next_cursor is not None, "endCursor": next_cursor}}


def repo(ident, language="Python", stars=1, private=False):
    return dict(id=ident, nameWithOwner=f"owner/{ident}", isPrivate=private, isFork=False,
                stargazerCount=stars, primaryLanguage={"name": language} if language else None)


def commit(oid, author="jordan", parents=1, additions=10, deletions=2):
    return dict(oid=oid, additions=additions, deletions=deletions,
                author={"user": {"id": author} if author else None}, parents={"totalCount": parents})


class FakeGitHub:
    def __init__(self):
        self.calls = []

    def query(self, query, **v):
        self.calls.append((query, v))
        if "followers" in query:
            return {"user": {"id": "jordan", "createdAt": "2023-11-16T00:00:00Z", "followers": {"totalCount": 5}}}
        if "ownerAffiliations" in query:
            return {"user": {"repositories": connection([repo("empty", None, 2)]) if v["after"] else connection([repo("own")], "page2")}}
        if "repositoriesContributedTo" in query:
            return {"user": {"repositoriesContributedTo": connection([repo("collab", "C++", 90)])}}
        if "contributionsCollection" in query:
            return {"user": {"contributionsCollection": {"commitContributionsByRepository": [
                {"repository": repo("collab")}, {"repository": repo("hidden", private=True)}]}}}
        if "repository(owner" in query:
            return {"repository": repo("collab")}
        if v["id"] == "empty":
            return {"node": {"defaultBranchRef": None}}
        if v["id"] == "own":
            history = connection([commit("shared"), commit("merge", parents=2), commit("wrong", author="someone"), commit("unknown", author=None)], "next") if not v["after"] else connection([commit("own", additions=7, deletions=3)])
        else:
            history = connection([commit("shared"), commit("collab", additions=20, deletions=4)])
        return {"node": {"defaultBranchRef": {"target": {"history": history}}}}


class StatsTests(unittest.TestCase):
    def test_aggregation_public_collaborations_pagination_and_empty_repo(self):
        api = FakeGitHub()
        result = stats.collect(api, NOW)
        self.assertEqual((result["repos"], result["stars"], result["followers"]), (2, 3, 5))
        self.assertEqual((result["commits"], result["additions"], result["deletions"]), (3, 37, 9))
        self.assertEqual(result["languages"], ["Python"])
        history = [v for q, v in api.calls if "defaultBranchRef" in q]
        self.assertTrue(all(v["author"] == "jordan" for v in history))
        self.assertNotIn("hidden", [v["id"] for v in history])
        self.assertTrue(any(v["after"] == "next" for v in history))

    def test_discovery_covers_account_history_in_year_windows(self):
        api = FakeGitHub()
        start = datetime(2023, 11, 16, tzinfo=timezone.utc)
        stats.historical_repos(api, start, NOW)
        calls = [v for _, v in api.calls]
        self.assertEqual(len(calls), 3)
        self.assertEqual(calls[0]["from"], stats.iso(start))
        self.assertEqual(calls[-1]["to"], stats.iso(NOW))
        for left, right in zip(calls, calls[1:]):
            self.assertEqual(left["to"], right["from"])

    def test_capped_discovery_splits_window(self):
        class Capped:
            def query(self, query, **v):
                span = datetime.fromisoformat(v["to"]) - datetime.fromisoformat(v["from"])
                rows = [{"repository": repo("a")}] * (100 if span > timedelta(days=1) else 1)
                return {"user": {"contributionsCollection": {"commitContributionsByRepository": rows}}}
        self.assertEqual(len(stats.historical_repos(Capped(), NOW - timedelta(days=2), NOW)), 2)

    def test_unsplittable_cap_fails(self):
        class Capped:
            def query(self, query, **v):
                return {"user": {"contributionsCollection": {"commitContributionsByRepository": [None] * 100}}}
        with self.assertRaises(stats.CollectionError):
            stats.historical_repos(Capped(), NOW - timedelta(seconds=1), NOW)

    def test_invalid_pagination_fails(self):
        with self.assertRaises(stats.CollectionError):
            list(stats.pages(lambda cursor: connection([], "same")))

    def test_uptime_anniversary_and_month_boundary(self):
        created = "2023-11-16T12:00:00Z"
        for date, expected in (("2026-11-15", "2 years, 11 months"), ("2026-11-16", "3 years, 0 months"),
                               ("2023-11-16", "0 years, 0 months"), ("2024-01-01", "0 years, 1 month")):
            self.assertEqual(stats.uptime(created, datetime.fromisoformat(date).replace(tzinfo=timezone.utc)), expected + " on GitHub")

    def test_refresh_is_deterministic_and_failure_preserves_assets(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory)
            stats.generate(FakeGitHub(), NOW, output)
            before = {p.name: (p.read_bytes(), p.stat().st_mtime_ns) for p in output.iterdir()}
            stats.generate(FakeGitHub(), NOW, output)
            self.assertEqual(before, {p.name: (p.read_bytes(), p.stat().st_mtime_ns) for p in output.iterdir()})
            with patch.object(stats, "collect", side_effect=stats.CollectionError("API unavailable")):
                with self.assertRaises(stats.CollectionError):
                    stats.generate(FakeGitHub(), NOW, output)
            self.assertEqual(before, {p.name: (p.read_bytes(), p.stat().st_mtime_ns) for p in output.iterdir()})
            for data, _ in before.values():
                ET.fromstring(data)

    def test_auth_and_partial_graphql_errors_are_not_retried(self):
        with self.assertRaises(stats.CollectionError):
            stats.GitHub("")
        for failure in (HTTPError("url", 401, "Unauthorized", {}, None),
                        BytesIO(b'{"data":{"partial":1},"errors":[{"message":"denied"}]}')):
            with patch.object(stats.request, "urlopen", side_effect=failure if isinstance(failure, Exception) else None,
                              return_value=failure) as urlopen:
                with self.assertRaises(stats.CollectionError):
                    stats.GitHub("test").query("query")
                self.assertEqual(urlopen.call_count, 1)

    def test_network_retries_are_bounded(self):
        with patch.object(stats.request, "urlopen", side_effect=URLError("offline")) as urlopen, patch.object(stats.time, "sleep"):
            with self.assertRaises(stats.CollectionError):
                stats.GitHub("test").query("query")
            self.assertEqual(urlopen.call_count, 4)

    def test_svg_xml_escaping_and_hero_animation_timelines(self):
        data = stats.collect(FakeGitHub(), NOW)
        data["languages"] = ["A&B", "<Test>"]
        for theme in stats.PALETTES:
            ET.fromstring(stats.render(data, theme, NOW))
            root = ET.fromstring(hero.hero(theme))
            ids = [node.attrib["id"] for node in root.iter() if "id" in node.attrib]
            self.assertEqual(len(ids), len(set(ids)))
            for node in root.iter("{http://www.w3.org/2000/svg}animate"):
                if "keyTimes" in node.attrib:
                    times = [float(t) for t in node.attrib["keyTimes"].split(";")]
                    self.assertEqual(times, sorted(set(times)))
                    self.assertEqual((times[0], times[-1]), (0, 1))
                    self.assertEqual(len(times), len(node.attrib["values"].split(";")))


if __name__ == "__main__":
    unittest.main()
