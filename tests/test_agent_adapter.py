"""Tests for AgentAdapter and Agent Capabilities Matrix (herdr/agent_adapter.py)."""

from unittest.mock import MagicMock, patch
import pytest

from herdr.agent_adapter import (
    AgentAdapter,
    AgentCapability,
    TTYSteeringPrototypeAdapter,
    ClaudeAdapter,
    CodexAdapter,
    OpenCodeAdapter,
    QoderAdapter,
    AgyAdapter,
    PiAdapter,
    get_agent_adapter,
    register_agent_adapter,
    list_agent_adapters,
)


def test_agent_capability_dataclass():
    cap = AgentCapability(
        supports_interrupt=True,
        supports_soft_steer=False,
        supports_resume=False,
        supports_prompt_injection=True,
        protocol_level="tty_prototype",
    )
    assert cap.supports_interrupt is True
    assert cap.supports_soft_steer is False
    assert cap.supports_resume is False
    assert cap.supports_prompt_injection is True
    assert cap.protocol_level == "tty_prototype"

    d = cap.to_dict()
    assert d["supports_interrupt"] is True
    assert d["protocol_level"] == "tty_prototype"


def test_known_agent_adapters_capability_matrix():
    # Claude
    claude = get_agent_adapter("claude")
    assert isinstance(claude, ClaudeAdapter)
    assert claude.name == "claude"
    assert claude.supports_interrupt is True
    assert claude.supports_soft_steer is True
    assert claude.supports_resume is True
    assert claude.supports_prompt_injection is True
    assert claude.protocol_level == "tty_prototype"

    # Codex
    codex = get_agent_adapter("codex")
    assert isinstance(codex, CodexAdapter)
    assert codex.name == "codex"
    assert codex.supports_interrupt is True
    assert codex.supports_soft_steer is True
    assert codex.supports_resume is True
    assert codex.supports_prompt_injection is True

    # OpenCode
    opencode = get_agent_adapter("opencode")
    assert isinstance(opencode, OpenCodeAdapter)
    assert opencode.name == "opencode"
    assert opencode.supports_interrupt is True
    assert opencode.supports_soft_steer is False
    assert opencode.supports_resume is False
    assert opencode.supports_prompt_injection is True

    # Qoder / qodercli
    qoder = get_agent_adapter("qodercli")
    assert isinstance(qoder, QoderAdapter)
    assert qoder.name == "qodercli"
    assert qoder.supports_interrupt is True
    assert qoder.supports_soft_steer is False

    # Agy
    agy = get_agent_adapter("agy")
    assert isinstance(agy, AgyAdapter)
    assert agy.name == "agy"
    assert agy.supports_interrupt is True
    assert agy.supports_resume is True

    # Pi
    pi = get_agent_adapter("pi")
    assert isinstance(pi, PiAdapter)
    assert pi.name == "pi"
    assert pi.supports_interrupt is True
    assert pi.supports_resume is False


def test_adapter_registry_aliases_and_fallback():
    # Alias qoder -> qodercli
    q1 = get_agent_adapter("qoder")
    q2 = get_agent_adapter("qodercn")
    assert isinstance(q1, QoderAdapter)
    assert isinstance(q2, QoderAdapter)

    # Unknown agent falls back to TTYSteeringPrototypeAdapter
    fallback = get_agent_adapter("unknown-llm-bot")
    assert isinstance(fallback, TTYSteeringPrototypeAdapter)
    assert fallback.name == "tty_prototype"
    assert fallback.protocol_level == "tty_prototype"

    # None falls back to TTYSteeringPrototypeAdapter
    none_adapter = get_agent_adapter(None)
    assert isinstance(none_adapter, TTYSteeringPrototypeAdapter)


def test_list_agent_adapters():
    adapters = list_agent_adapters()
    assert "claude" in adapters
    assert "codex" in adapters
    assert "opencode" in adapters
    assert "qodercli" in adapters
    assert "agy" in adapters
    assert "pi" in adapters
    assert "tty_prototype" in adapters

    claude_info = adapters["claude"]
    assert claude_info["capabilities"]["supports_interrupt"] is True
    assert claude_info["protocol_level"] == "tty_prototype"


def test_custom_agent_adapter_registration():
    class CustomMockAdapter(AgentAdapter):
        name = "custom-mock"
        capabilities = AgentCapability(
            supports_interrupt=False,
            supports_soft_steer=True,
            supports_resume=False,
            supports_prompt_injection=True,
            protocol_level="native_rpc",
        )

    custom = CustomMockAdapter()
    register_agent_adapter(custom)

    retrieved = get_agent_adapter("custom-mock")
    assert retrieved.name == "custom-mock"
    assert retrieved.supports_interrupt is False
    assert retrieved.protocol_level == "native_rpc"


def test_adapter_steer_urgent():
    adapter = CodexAdapter()
    mock_run = MagicMock()
    mock_run.return_value.returncode = 0

    with patch("subprocess.run", mock_run):
        res = adapter.steer_urgent("pane-123", "Stop and refactor", operator="tester", wait_after_interrupt=0.01)

    assert res["ok"] is True
    assert res["interrupted"] is True
    assert res["injected"] is True
    assert res["adapter"] == "codex"
    assert res["protocol_level"] == "tty_prototype"

    # Verify subprocess calls: send-keys ctrl-c, send-text prompt, send-keys enter
    calls = mock_run.call_args_list
    assert len(calls) == 3
    assert "ctrl-c" in calls[0][0][0]
    assert "send-text" in calls[1][0][0]
    assert "Stop and refactor" in calls[1][0][0][4]
    assert "enter" in calls[2][0][0]


def test_adapter_steer_soft():
    adapter = ClaudeAdapter()
    mock_run = MagicMock()
    mock_run.return_value.returncode = 0

    with patch("subprocess.run", mock_run):
        res = adapter.steer_soft("pane-456", "Keep standard library only", operator="alice")

    assert res["ok"] is True
    assert res["interrupted"] is False
    assert res["injected"] is True
    assert res["adapter"] == "claude"

    calls = mock_run.call_args_list
    # Soft steer does NOT call ctrl-c
    assert not any("ctrl-c" in c[0][0] for c in calls)
    assert any("send-text" in c[0][0] for c in calls)
    assert any("enter" in c[0][0] for c in calls)


def test_adapter_unsupported_interrupt():
    class NoInterruptAdapter(AgentAdapter):
        name = "no-interrupt"
        capabilities = AgentCapability(supports_interrupt=False)

    adapter = NoInterruptAdapter()
    mock_run = MagicMock()
    with patch("subprocess.run", mock_run):
        ok = adapter.interrupt("pane-999", reason="test")
        res = adapter.steer_urgent("pane-999", "Emergency stop", wait_after_interrupt=0.0)

    assert ok is False
    assert res["interrupted"] is False
    assert not any("ctrl-c" in c[0][0] for c in mock_run.call_args_list)


def test_adapter_resume():
    codex = CodexAdapter()
    opencode = OpenCodeAdapter()

    mock_run = MagicMock()
    mock_run.return_value.returncode = 0

    with patch("subprocess.run", mock_run):
        assert codex.resume("pane-111") is True
        assert opencode.resume("pane-222") is False
