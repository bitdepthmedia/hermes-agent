import importlib
import os
import unittest


class CallOrchestratorToolTests(unittest.TestCase):
    def setUp(self):
        self.previous_env = os.environ.copy()

    def tearDown(self):
        os.environ.clear()
        os.environ.update(self.previous_env)

    def test_schema_uses_primary_orchestrator_naming(self):
        tool = importlib.import_module("tools.call_orchestrator_tool")

        self.assertEqual(tool.CALL_ORCHESTRATOR_SCHEMA["name"], "call_orchestrator")
        serialized = str(tool.CALL_ORCHESTRATOR_SCHEMA).lower()
        self.assertNotIn("bert", serialized)

    def test_requirements_use_neutral_environment_names(self):
        tool = importlib.import_module("tools.call_orchestrator_tool")
        os.environ["HERMES_ORCHESTRATOR_API_KEY"] = "test-key"
        os.environ["HERMES_ORCHESTRATOR_BASE_URL"] = "http://127.0.0.1:8643/v1"

        self.assertTrue(tool.check_call_orchestrator_requirements())


if __name__ == "__main__":
    unittest.main()
