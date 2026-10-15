"""Offline checks: no live credentials, containers, databases or cloud APIs."""
import importlib.util
import json
import os
from pathlib import Path
import re
import shutil
import stat
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("init_server_env", ROOT / "scripts/init_server_env.py")
initializer = importlib.util.module_from_spec(spec)
spec.loader.exec_module(initializer)
REGISTRY = "crpi-test.cn-hangzhou.personal.cr.aliyuncs.com"
TAG = "sha-" + "a" * 40 + "-123-1"


class ServerEnvironmentTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.target = Path(self.directory.name) / ".env.server"
        self.template = ROOT / "deploy/server.env.example"

    def create(self, registry=REGISTRY, tag=TAG):
        initializer.create_env(self.template, self.target, registry, tag)

    def values(self):
        return dict(line.split("=", 1) for line in self.target.read_text().splitlines()
                    if line and not line.startswith("#"))

    def test_password_is_random_and_matches_database_url(self):
        self.create()
        first = self.values()
        self.assertTrue(re.fullmatch(r"[0-9a-f]{64}", first["POSTGRES_PASSWORD"]))
        self.assertTrue(first["POSTGRES_PASSWORD"] in first["DATABASE_URL"])
        self.assertEqual(first["ACR_REGISTRY"], REGISTRY)
        self.assertEqual(first["RELEASE_TAG"], TAG)
        self.assertEqual(first["OSS_ACCESS_KEY_SECRET"], "")
        self.assertEqual(first["APP_ENV"], "development")
        self.assertEqual(first["APP_ORIGIN"], "http://127.0.0.1:8080")
        if os.name != "nt":
            self.assertEqual(stat.S_IMODE(self.target.stat().st_mode), 0o600)
        second = self.target.with_name("second.env")
        initializer.create_env(self.template, second, REGISTRY, TAG)
        self.assertNotIn(first["POSTGRES_PASSWORD"], second.read_text())

    def test_existing_configuration_is_never_replaced(self):
        self.target.write_text("keep-existing-configuration\n")
        with self.assertRaises(FileExistsError):
            self.create()
        self.assertEqual(self.target.read_text(), "keep-existing-configuration\n")

    def test_rejects_unexpected_registry_without_creating_file(self):
        for value in ["https://" + REGISTRY, "docker.io", REGISTRY + "/ai-notes", REGISTRY + "\nOTHER=value"]:
            with self.subTest(registry=value), self.assertRaises(ValueError):
                self.create(registry=value)
            self.assertFalse(self.target.exists())

    def test_rejects_floating_or_malformed_release_tag(self):
        for tag in ["latest", "sha-abc", TAG + "\nOTHER=value"]:
            with self.subTest(tag=tag), self.assertRaises(ValueError):
                self.create(tag=tag)
            self.assertFalse(self.target.exists())

    def test_vpc_registry_is_accepted_for_later_verified_use(self):
        self.create(registry="crpi-test-vpc.cn-hangzhou.personal.cr.aliyuncs.com")
        self.assertTrue(self.values()["ACR_REGISTRY"].startswith("crpi-test-vpc."))

    @unittest.skipUnless(shutil.which("docker"), "Docker Compose client is required")
    def test_compose_uses_only_acr_images_and_private_ports(self):
        self.create()
        result = subprocess.run(
            ["docker", "compose", "--project-directory", self.directory.name,
             "--env-file", str(self.target), "-f", str(ROOT / "compose.server.yaml"),
             "--profile", "tools", "config", "--format", "json"],
            capture_output=True, text=True, timeout=30,
        )
        # Do not include resolved configuration in assertion output: it contains a generated secret.
        self.assertEqual(result.returncode, 0, "Compose configuration validation failed")
        services = json.loads(result.stdout)["services"]
        self.assertEqual(set(services), {"db", "migrate", "api", "worker", "web"})
        for service in services.values():
            self.assertTrue(service["image"].startswith(REGISTRY + "/ai-notes/"))
            self.assertTrue(service["image"].endswith(":" + TAG))
            self.assertNotIn("build", service)
        for name in ["db", "api", "worker", "migrate"]:
            self.assertFalse(services[name].get("ports"))
        ports = services["web"]["ports"]
        self.assertEqual(len(ports), 1)
        self.assertEqual(ports[0]["host_ip"], "127.0.0.1")
        self.assertEqual(str(ports[0]["published"]), "8080")
        self.assertEqual(services["migrate"]["profiles"], ["tools"])
        self.assertNotIn("migrate", services["api"]["depends_on"])
        self.assertLessEqual(sum(int(services[name]["mem_limit"]) for name in ["db", "api", "worker", "web"]), 3 * 1024 ** 3)


if __name__ == "__main__":
    unittest.main()
