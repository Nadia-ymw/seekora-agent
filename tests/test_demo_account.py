import unittest

from seekora_agent.application.profile import ProfileService
from seekora_agent.domain.test_account import build_default_test_account
from seekora_agent.infrastructure.stores.memory import InMemoryProfileStore


class DemoAccountTest(unittest.IsolatedAsyncioTestCase):
    async def test_default_account_seeds_an_authorized_profile(self):
        account = build_default_test_account()
        service = ProfileService(InMemoryProfileStore([account.initial_profile]))

        profile = await service.get(account.tenant_id, account.user_id)

        self.assertTrue(profile.consent.personalization_enabled)
        self.assertTrue(profile.consent.behavior_storage_enabled)
        self.assertEqual(("轻薄", "长续航"), profile.positive_preferences)

    async def test_seed_does_not_change_new_user_privacy_defaults(self):
        account = build_default_test_account()
        service = ProfileService(InMemoryProfileStore([account.initial_profile]))

        new_user = await service.get("demo", "another-user")

        self.assertFalse(new_user.consent.personalization_enabled)
        self.assertFalse(new_user.consent.behavior_storage_enabled)

    async def test_account_contains_no_authentication_secret(self):
        account = build_default_test_account()
        payload = account.as_dict()

        self.assertFalse(payload["authentication_enabled"])
        self.assertNotIn("password", repr(payload).lower())
        self.assertNotIn("token", repr(payload).lower())


if __name__ == "__main__":
    unittest.main()
