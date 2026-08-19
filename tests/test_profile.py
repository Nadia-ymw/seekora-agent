import unittest

from seekora_agent.application.profile import ConsentRequired, ProfileService
from seekora_agent.infrastructure.stores.memory import InMemoryProfileStore


class ProfileServiceTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.store = InMemoryProfileStore()
        self.service = ProfileService(self.store)

    async def test_missing_profile_defaults_to_no_consent_without_persisting(self):
        profile = await self.service.get("demo", "user-1")

        self.assertFalse(profile.consent.personalization_enabled)
        self.assertFalse(profile.consent.behavior_storage_enabled)
        self.assertIsNone(await self.store.get("demo", "user-1"))

    async def test_preferences_require_explicit_personalization_consent(self):
        with self.assertRaises(ConsentRequired):
            await self.service.replace_preferences(
                "demo", "user-1", ["轻薄"], ["游戏本"]
            )

    async def test_preferences_are_normalized_after_consent(self):
        await self.service.update_consent("demo", "user-1", True, False)
        profile = await self.service.replace_preferences(
            "demo", "user-1", [" 轻薄 ", "轻薄", "长续航"], ["厚重"]
        )

        self.assertEqual(("轻薄", "长续航"), profile.positive_preferences)
        self.assertEqual(("厚重",), profile.negative_preferences)
        self.assertEqual(2, profile.version)

    async def test_disabled_consent_blocks_ranking_but_preserves_user_data(self):
        await self.service.update_consent("demo", "user-1", True, False)
        await self.service.replace_preferences("demo", "user-1", ["静音"], [])
        profile = await self.service.update_consent("demo", "user-1", False, False)

        self.assertEqual(("静音",), profile.positive_preferences)
        self.assertIsNone(await self.service.ranking_snapshot("demo", "user-1"))

    async def test_profile_isolated_by_tenant_and_can_be_deleted(self):
        await self.service.update_consent("tenant-a", "same-user", True, True)

        other = await self.service.get("tenant-b", "same-user")
        self.assertFalse(other.consent.personalization_enabled)
        self.assertTrue(await self.service.delete("tenant-a", "same-user"))
        self.assertFalse(await self.service.delete("tenant-a", "same-user"))


if __name__ == "__main__":
    unittest.main()
