import base64
import importlib.util
from pathlib import Path
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("init_ai_secret", ROOT / "scripts/init_ai_secret.py")
initializer = importlib.util.module_from_spec(spec)
spec.loader.exec_module(initializer)


class AiSecretInitializerTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.target = self.root / ".env"

    def test_initializes_once_without_replacing_other_configuration(self):
        self.target.write_text("DATABASE_URL=keep-existing-value\n", encoding="utf-8")
        self.assertTrue(initializer.initialize(self.root, ".env"))
        first = self.target.read_text(encoding="utf-8")
        self.assertIn("DATABASE_URL=keep-existing-value\n", first)
        encoded = first.split(initializer.NAME + "=", 1)[1].strip()
        self.assertEqual(len(base64.urlsafe_b64decode(encoded)), 32)
        self.assertFalse(initializer.initialize(self.root, ".env"))
        self.assertEqual(first, self.target.read_text(encoding="utf-8"))

    def test_fills_empty_setting_without_duplicates(self):
        self.target.write_text(initializer.NAME + "=\n", encoding="utf-8")
        self.assertTrue(initializer.initialize(self.root, ".env"))
        self.assertEqual(self.target.read_text().count(initializer.NAME + "="), 1)

    def test_rejects_duplicate_settings_and_public_templates(self):
        initial = initializer.NAME + "=\n" + initializer.NAME + "=\n"
        self.target.write_text(initial, encoding="utf-8")
        with self.assertRaises(ValueError):
            initializer.initialize(self.root, ".env")
        self.assertEqual(initial, self.target.read_text())
        template = self.root / ".env.example"
        template.write_text("public-template", encoding="utf-8")
        with self.assertRaises(ValueError):
            initializer.initialize(self.root, ".env.example")
        self.assertEqual(template.read_text(), "public-template")

    def test_rejects_targets_outside_the_workspace(self):
        with tempfile.TemporaryDirectory() as elsewhere:
            target = Path(elsewhere) / ".env"
            target.write_text("private-file", encoding="utf-8")
            with self.assertRaises(ValueError):
                initializer.initialize(self.root, str(target))
            self.assertEqual(target.read_text(), "private-file")


if __name__ == "__main__":
    unittest.main()
