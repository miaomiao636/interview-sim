import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient

from backend.main import app
from backend.main import health


class HealthEndpointTests(unittest.IsolatedAsyncioTestCase):
    async def test_health_reports_provider_readiness_without_exposing_key(self):
        with patch("backend.main.config.XIAOMI_API_KEY", "secret-value"):
            result = await health()

        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["provider"], "MiMo")
        self.assertTrue(result["api_key_configured"])
        self.assertNotIn("secret-value", str(result))
        self.assertNotIn("port", result)


class LocalHostBoundaryTests(unittest.TestCase):
    def test_rejects_untrusted_host_headers(self):
        with TestClient(app) as client:
            response = client.get("/api/health", headers={"Host": "attacker.example"})

        self.assertEqual(response.status_code, 400)


if __name__ == "__main__":
    unittest.main()
