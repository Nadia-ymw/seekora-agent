import tempfile
import unittest
from pathlib import Path

from seekora_agent.application.profile import ProfileService
from seekora_agent.domain.profile import ConsentState, LongTermProfile
from seekora_agent.infrastructure.stores.sqlite_profile import SQLiteProfileStore


class SQLiteProfileStoreTest(unittest.IsolatedAsyncioTestCase):
    async def test_profile_survives_store_recreation_and_can_be_deleted(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "profiles.sqlite3"
            first = ProfileService(SQLiteProfileStore(path))
            await first.update_consent("tenant-a", "user-1", True, True)
            await first.replace_preferences("tenant-a", "user-1", ["轻薄"], ["厚重"])

            second = ProfileService(SQLiteProfileStore(path))
            restored = await second.get("tenant-a", "user-1")

            self.assertEqual(("轻薄",), restored.positive_preferences)
            self.assertEqual(("厚重",), restored.negative_preferences)
            self.assertTrue(restored.consent.personalization_enabled)
            self.assertEqual(2, restored.version)
            self.assertTrue(await second.delete("tenant-a", "user-1"))
            self.assertIsNone(await SQLiteProfileStore(path).get("tenant-a", "user-1"))

    async def test_seed_does_not_overwrite_existing_consent(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "profiles.sqlite3"
            seed = LongTermProfile(
                tenant_id="demo",
                user_id="seed-user",
                consent=ConsentState(True, True),
            )
            service = ProfileService(SQLiteProfileStore(path, [seed]))
            await service.update_consent("demo", "seed-user", False, False)

            restored = await SQLiteProfileStore(path, [seed]).get("demo", "seed-user")

            self.assertFalse(restored.consent.personalization_enabled)
            self.assertFalse(restored.consent.behavior_storage_enabled)


if __name__ == "__main__":
    unittest.main()
