"""Unit tests for flair metadata cache behavior."""

import unittest
from unittest.mock import patch

from tests._env import ensure_test_env

ensure_test_env()

from temporal.activities import flair as flair_activities


class _FakeFlairEndpoint:
    def __init__(self, templates, flair_text_by_user=None):
        self._templates = list(templates)
        self._flair_text_by_user = flair_text_by_user or {}
        self.templates_reads = 0
        self.user_reads = 0

    @property
    def templates(self):
        self.templates_reads += 1
        return list(self._templates)

    def __call__(self, username):
        self.user_reads += 1
        yield {"flair_text": self._flair_text_by_user.get(username)}


class _FakeSubreddit:
    def __init__(self, templates, moderators):
        self.flair = _FakeFlairEndpoint(templates)
        self._moderators = list(moderators)
        self.moderator_reads = 0

    def moderator(self):
        self.moderator_reads += 1
        return list(self._moderators)


class _FakeClock:
    def __init__(self, start=1000.0):
        self.now = float(start)

    def monotonic(self):
        return self.now


class FlairMetadataCacheTests(unittest.TestCase):
    def setUp(self):
        self.original_template_ttl = (
            flair_activities.FlairManager.FLAIR_TEMPLATE_CACHE_TTL_SECONDS
        )
        self.original_moderator_ttl = flair_activities.FlairManager.MODERATORS_CACHE_TTL_SECONDS
        flair_activities.FlairManager.invalidate_caches()

    def tearDown(self):
        flair_activities.FlairManager.invalidate_caches()
        flair_activities.FlairManager.FLAIR_TEMPLATE_CACHE_TTL_SECONDS = (
            self.original_template_ttl
        )
        flair_activities.FlairManager.MODERATORS_CACHE_TTL_SECONDS = (
            self.original_moderator_ttl
        )

    def test_template_and_moderator_cache_respects_ttl(self):
        templates = [
            {"id": "tmpl-1", "text": "Trades: 0-10", "mod_only": False},
            {"id": "tmpl-2", "text": "Trades: 0-10", "mod_only": True},
        ]
        subreddit = _FakeSubreddit(templates=templates, moderators=["mod_user"])
        clock = _FakeClock()

        flair_activities.FlairManager.FLAIR_TEMPLATE_CACHE_TTL_SECONDS = 10
        flair_activities.FlairManager.MODERATORS_CACHE_TTL_SECONDS = 10

        with patch.object(flair_activities.time, "monotonic", clock.monotonic):
            flair_activities.FlairManager._load_flair_templates(subreddit)
            flair_activities.FlairManager._load_moderators(subreddit)
            self.assertEqual(subreddit.flair.templates_reads, 1)
            self.assertEqual(subreddit.moderator_reads, 1)

            # Within TTL: cache is reused
            clock.now += 5
            flair_activities.FlairManager._load_flair_templates(subreddit)
            flair_activities.FlairManager._load_moderators(subreddit)
            self.assertEqual(subreddit.flair.templates_reads, 1)
            self.assertEqual(subreddit.moderator_reads, 1)

            # Beyond TTL: cache refreshes
            clock.now += 6
            flair_activities.FlairManager._load_flair_templates(subreddit)
            flair_activities.FlairManager._load_moderators(subreddit)
            self.assertEqual(subreddit.flair.templates_reads, 2)
            self.assertEqual(subreddit.moderator_reads, 2)


class ReloadFlairMetadataActivityTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.original_template_ttl = (
            flair_activities.FlairManager.FLAIR_TEMPLATE_CACHE_TTL_SECONDS
        )
        self.original_moderator_ttl = flair_activities.FlairManager.MODERATORS_CACHE_TTL_SECONDS
        flair_activities.FlairManager.invalidate_caches()

    async def asyncTearDown(self):
        flair_activities.FlairManager.invalidate_caches()
        flair_activities.FlairManager.FLAIR_TEMPLATE_CACHE_TTL_SECONDS = (
            self.original_template_ttl
        )
        flair_activities.FlairManager.MODERATORS_CACHE_TTL_SECONDS = (
            self.original_moderator_ttl
        )

    async def test_reload_flair_metadata_cache_forces_refresh(self):
        templates = [{"id": "tmpl-1", "text": "Trades: 0-10", "mod_only": False}]
        subreddit = _FakeSubreddit(templates=templates, moderators=["mod_a", "mod_b"])

        with (
            patch.object(flair_activities, "get_reddit_client", return_value=object()),
            patch.object(flair_activities, "get_subreddit", return_value=subreddit),
        ):
            result = await flair_activities.reload_flair_metadata_cache()

        self.assertEqual(result["templates"], 1)
        self.assertEqual(result["moderators"], 2)
        self.assertEqual(subreddit.flair.templates_reads, 1)
        self.assertEqual(subreddit.moderator_reads, 1)


if __name__ == "__main__":
    unittest.main()
