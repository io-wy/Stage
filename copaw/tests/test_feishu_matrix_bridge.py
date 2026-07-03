"""Tests for Feishu-Matrix bridge configuration helpers.

Full integration tests require lark-channel-sdk and matrix-nio to be installed.
"""
import os
from pathlib import Path

import pytest


def test_config_from_env(monkeypatch):
    from copaw_worker.feishu_matrix_bridge import FeishuMatrixBridgeConfig

    monkeypatch.setenv("FEISHU_APP_ID", "cli_test")
    monkeypatch.setenv("FEISHU_APP_SECRET", "secret_test")
    monkeypatch.setenv("FEISHU_DOMAIN", "https://open.larksuite.com")
    monkeypatch.setenv("MATRIX_HOMESERVER", "https://matrix.example.com")
    monkeypatch.setenv("MATRIX_USER_ID", "@bridge:example.com")
    monkeypatch.setenv("MATRIX_ACCESS_TOKEN", "token_test")
    monkeypatch.setenv("MATRIX_PASSWORD", "password_test")
    monkeypatch.setenv("MATRIX_DEVICE_NAME", "test-device")
    monkeypatch.setenv("MANAGER_MATRIX_USER_ID", "@manager:example.com")
    monkeypatch.setenv("FEISHU_BRIDGE_STATE_FILE", "/tmp/test_state.json")

    cfg = FeishuMatrixBridgeConfig.from_env()
    assert cfg.feishu_app_id == "cli_test"
    assert cfg.feishu_app_secret == "secret_test"
    assert cfg.feishu_domain == "https://open.larksuite.com"
    assert cfg.matrix_homeserver == "https://matrix.example.com"
    assert cfg.matrix_user_id == "@bridge:example.com"
    assert cfg.matrix_access_token == "token_test"
    assert cfg.matrix_password == "password_test"
    assert cfg.matrix_device_name == "test-device"
    assert cfg.manager_matrix_user_id == "@manager:example.com"
    assert cfg.state_file == Path("/tmp/test_state.json")


def test_config_defaults(monkeypatch):
    from copaw_worker.feishu_matrix_bridge import FeishuMatrixBridgeConfig

    # Clear relevant env vars so defaults are exercised.
    for key in (
        "FEISHU_APP_ID",
        "FEISHU_APP_SECRET",
        "FEISHU_DOMAIN",
        "MATRIX_HOMESERVER",
        "MATRIX_USER_ID",
        "MATRIX_ACCESS_TOKEN",
        "MATRIX_PASSWORD",
        "MATRIX_DEVICE_NAME",
        "MANAGER_MATRIX_USER_ID",
        "FEISHU_BRIDGE_STATE_FILE",
    ):
        monkeypatch.delenv(key, raising=False)

    cfg = FeishuMatrixBridgeConfig.from_env()
    assert cfg.feishu_domain == "https://open.feishu.cn"
    assert cfg.matrix_device_name == "feishu-matrix-bridge"
    assert cfg.state_file == Path("/tmp/feishu_matrix_bridge_state.json")


def test_state_persistence(tmp_path):
    from copaw_worker.feishu_matrix_bridge import FeishuMatrixBridgeConfig

    state_file = tmp_path / "state.json"
    cfg = FeishuMatrixBridgeConfig(
        feishu_app_id="x",
        feishu_app_secret="y",
        matrix_homeserver="z",
        manager_matrix_user_id="@m:z",
        state_file=state_file,
    )

    # The bridge requires the SDKs, so we only test the dataclass/state helpers
    # here. Integration behavior is covered by manual/lint checks.
    assert cfg.state_file == state_file
    assert state_file.exists() is False
