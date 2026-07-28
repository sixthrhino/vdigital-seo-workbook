"""Tests for agent/config.py

Loads config.py fresh under a unique module name per test (rather than via
the shared agent-module loader) so each test gets isolated module-level state
— config.py mirrors GOOGLE_API_KEY into os.environ as an import-time side
effect, and we want env-var overrides in one test to never leak into another.
"""

import importlib.util
import itertools
import os
from pathlib import Path

_CONFIG_PATH = Path(__file__).resolve().parent.parent / "src" / "seo_testing_agent" / "config.py"
_counter = itertools.count()


def _fresh_config_module():
    name = f"_agent_config_test_{next(_counter)}"
    spec = importlib.util.spec_from_file_location(name, _CONFIG_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class TestSettings:
    def test_env_vars_override_dotenv_file(self, monkeypatch):
        monkeypatch.setenv("AGENT_MODEL", "gemini-test-model")
        monkeypatch.setenv("ENVIRONMENT", "staging")
        monkeypatch.setenv("MCP_SERVER_URL", "https://mcp.example.com")

        cfg = _fresh_config_module()

        assert cfg.settings.agent_model == "gemini-test-model"
        assert cfg.settings.environment == "staging"
        assert cfg.settings.mcp_server_url == "https://mcp.example.com"

    def test_google_api_key_mirrored_to_os_environ(self, monkeypatch):
        monkeypatch.delenv("GOOGLE_API_KEY", raising=False)
        monkeypatch.setenv("GOOGLE_API_KEY", "test-key-abc")

        cfg = _fresh_config_module()

        assert cfg.settings.google_api_key == "test-key-abc"
        assert os.environ["GOOGLE_API_KEY"] == "test-key-abc"

    def test_empty_google_api_key_is_not_forced_into_os_environ(self, monkeypatch):
        monkeypatch.delenv("GOOGLE_API_KEY", raising=False)
        monkeypatch.setenv("GOOGLE_API_KEY", "")

        calls = []
        original_setdefault = os.environ.setdefault
        def spy_setdefault(key, value):
            calls.append((key, value))
            return original_setdefault(key, value)
        monkeypatch.setattr(os.environ, "setdefault", spy_setdefault)

        cfg = _fresh_config_module()

        assert cfg.settings.google_api_key == ""
        assert calls == []

    def test_field_defaults(self):
        # Class-level field defaults, independent of whatever agent/.env
        # happens to contain locally.
        cfg = _fresh_config_module()
        assert cfg.Settings.model_fields["agent_model"].default == "gemini-2.0-flash"
        assert cfg.Settings.model_fields["gcp_location"].default == "us-central1"
        assert cfg.Settings.model_fields["environment"].default == "development"
        assert cfg.Settings.model_fields["mcp_server_url"].default == "http://localhost:8080"
