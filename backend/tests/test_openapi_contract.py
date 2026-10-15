import unittest

from app.main import app


class OpenAPIContractTests(unittest.TestCase):
    def test_validation_response_description_is_stable_across_python_versions(self):
        descriptions = {
            operation["responses"]["422"]["description"]
            for path in app.openapi()["paths"].values()
            for operation in path.values()
            if "422" in operation.get("responses", {})
        }
        self.assertEqual(descriptions, {"Unprocessable Content"})
