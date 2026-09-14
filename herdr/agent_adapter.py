#!/usr/bin/env python3
"""Agent Adapter Matrix & Intervention Primitives (herdr/agent_adapter.py).

Formalizes the abstraction layer between high-level orchestration / steering
and heterogeneous Agent runtimes (Claude, Codex, OpenCode, Qoder, Agy, Pi, etc.).

Explicitly defines the current terminal-based steering mechanism as:
    TTY-level steering prototype
rather than a universal agent steering protocol, because different agents
diverge significantly in their handling of:
- Ctrl-C interrupts
- Prompt injection & stdin parsing
- Multi-turn session state retention
- Post-interrupt session resume
"""

from dataclasses import dataclass
from datetime import datetime
import subprocess
import time
from typing import Any, Dict, Optional


@dataclass(frozen=True)
class AgentCapability:
    """Declared runtime capabilities of an Agent Adapter."""

    supports_interrupt: bool = True  # Can handle SIGINT / ctrl-c soft halt
    supports_soft_steer: bool = True  # Can receive prompt injection during idle gap without interrupt
    supports_resume: bool = True  # Supports resuming execution context after interrupt
    supports_prompt_injection: bool = True  # Can accept text prompts via stdin / pane
    protocol_level: str = "tty_prototype"  # "tty_prototype" | "native_rpc" | "api"

    def to_dict(self) -> Dict[str, Any]:
        return {
            "supports_interrupt": self.supports_interrupt,
            "supports_soft_steer": self.supports_soft_steer,
            "supports_resume": self.supports_resume,
            "supports_prompt_injection": self.supports_prompt_injection,
            "protocol_level": self.protocol_level,
        }


class AgentAdapter:
    """Base adapter for Agent execution runtimes."""

    name: str = "base"
    capabilities: AgentCapability = AgentCapability()

    @property
    def supports_interrupt(self) -> bool:
        return self.capabilities.supports_interrupt

    @property
    def supports_soft_steer(self) -> bool:
        return self.capabilities.supports_soft_steer

    @property
    def supports_resume(self) -> bool:
        return self.capabilities.supports_resume

    @property
    def supports_prompt_injection(self) -> bool:
        return self.capabilities.supports_prompt_injection

    @property
    def protocol_level(self) -> str:
        return self.capabilities.protocol_level

    def send_keys(self, pane_id: str, key: str) -> bool:
        """Send keystroke (e.g. enter, ctrl-c) to Herdr pane."""
        try:
            r = subprocess.run(
                ["herdr", "pane", "send-keys", pane_id, key],
                text=True,
                capture_output=True,
                timeout=10,
            )
            return r.returncode == 0
        except Exception:
            return False

    def send_text(self, pane_id: str, text: str) -> bool:
        """Send text prompt to Herdr pane."""
        try:
            r = subprocess.run(
                ["herdr", "pane", "send-text", pane_id, text],
                text=True,
                capture_output=True,
                timeout=10,
            )
            return r.returncode == 0
        except Exception:
            return False

    def format_steer_prompt(self, instruction: str, operator: str = "human") -> str:
        """Format structured high-priority intervention prompt for Agent."""
        now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        return (
            "\n━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
            f"【总指挥实时插话纠偏指令 - STEERING INSTRUCTION】\n"
            f"发起人：{operator} | 时间：{now_str}\n"
            "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
            "总指挥向你发送了高优先级干预指令，请立即优先吸收并按此调整后续动作：\n"
            f"> {instruction}\n"
            "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        )

    def interrupt(self, pane_id: str, reason: str = "") -> bool:
        """Interrupt running agent. TTY prototype sends ctrl-c."""
        if not self.supports_interrupt:
            return False
        return self.send_keys(pane_id, "ctrl-c")

    def inject_prompt(self, pane_id: str, prompt: str) -> bool:
        """Inject prompt into agent pane. TTY prototype sends text followed by enter."""
        if not self.supports_prompt_injection:
            return False
        ok = self.send_text(pane_id, prompt)
        if ok:
            return self.send_keys(pane_id, "enter")
        return False

    def steer_urgent(
        self,
        pane_id: str,
        instruction: str,
        operator: str = "human",
        wait_after_interrupt: float = 0.1,
    ) -> Dict[str, Any]:
        """Execute urgent steering intervention: interrupt (if supported) + inject prompt."""
        interrupted = False
        if self.supports_interrupt:
            interrupted = self.interrupt(pane_id, reason="urgent_steer")
            if wait_after_interrupt > 0:
                time.sleep(wait_after_interrupt)

        prompt = self.format_steer_prompt(instruction, operator=operator)
        injected = self.inject_prompt(pane_id, prompt)
        return {
            "ok": injected,
            "interrupted": interrupted,
            "injected": injected,
            "adapter": self.name,
            "protocol_level": self.protocol_level,
            "warning": None if self.supports_interrupt else f"Agent '{self.name}' does not support interrupt signal",
        }

    def steer_soft(
        self,
        pane_id: str,
        instruction: str,
        operator: str = "human",
    ) -> Dict[str, Any]:
        """Execute non-interrupting soft steering when agent is in an idle gap."""
        prompt = self.format_steer_prompt(instruction, operator=operator)
        injected = self.inject_prompt(pane_id, prompt)
        return {
            "ok": injected,
            "interrupted": False,
            "injected": injected,
            "adapter": self.name,
            "protocol_level": self.protocol_level,
            "warning": None if self.supports_soft_steer else f"Agent '{self.name}' does not fully support soft steer; input injected via TTY fallback",
        }

    def resume(self, pane_id: str, **kwargs) -> bool:
        """Resume execution if supported."""
        if not self.supports_resume:
            return False
        return self.send_keys(pane_id, "enter")

    def to_dict(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "capabilities": self.capabilities.to_dict(),
            "protocol_level": self.protocol_level,
        }


class TTYSteeringPrototypeAdapter(AgentAdapter):
    """Fallback adapter representing the generic TTY-level steering prototype."""

    name = "tty_prototype"
    capabilities = AgentCapability(
        supports_interrupt=True,
        supports_soft_steer=True,
        supports_resume=False,
        supports_prompt_injection=True,
        protocol_level="tty_prototype",
    )


class ClaudeAdapter(AgentAdapter):
    """Adapter for Anthropic Claude Code CLI."""

    name = "claude"
    capabilities = AgentCapability(
        supports_interrupt=True,
        supports_soft_steer=True,
        supports_resume=True,
        supports_prompt_injection=True,
        protocol_level="tty_prototype",
    )


class CodexAdapter(AgentAdapter):
    """Adapter for OpenAI Codex CLI."""

    name = "codex"
    capabilities = AgentCapability(
        supports_interrupt=True,
        supports_soft_steer=True,
        supports_resume=True,
        supports_prompt_injection=True,
        protocol_level="tty_prototype",
    )


class OpenCodeAdapter(AgentAdapter):
    """Adapter for OpenCode CLI.
    
    OpenCode in auto-mode runs tool loops; soft-steer without interrupt
    gets ignored or swallowed by active tool executions.
    """

    name = "opencode"
    capabilities = AgentCapability(
        supports_interrupt=True,
        supports_soft_steer=False,
        supports_resume=False,
        supports_prompt_injection=True,
        protocol_level="tty_prototype",
    )


class QoderAdapter(AgentAdapter):
    """Adapter for Qoder CLI."""

    name = "qodercli"
    capabilities = AgentCapability(
        supports_interrupt=True,
        supports_soft_steer=False,
        supports_resume=False,
        supports_prompt_injection=True,
        protocol_level="tty_prototype",
    )


class AgyAdapter(AgentAdapter):
    """Adapter for Google Antigravity (Agy) CLI."""

    name = "agy"
    capabilities = AgentCapability(
        supports_interrupt=True,
        supports_soft_steer=True,
        supports_resume=True,
        supports_prompt_injection=True,
        protocol_level="tty_prototype",
    )


class PiAdapter(AgentAdapter):
    """Adapter for Pi CLI."""

    name = "pi"
    capabilities = AgentCapability(
        supports_interrupt=True,
        supports_soft_steer=True,
        supports_resume=False,
        supports_prompt_injection=True,
        protocol_level="tty_prototype",
    )


# Adapter registry
_ADAPTER_REGISTRY: Dict[str, AgentAdapter] = {
    "tty_prototype": TTYSteeringPrototypeAdapter(),
    "claude": ClaudeAdapter(),
    "codex": CodexAdapter(),
    "opencode": OpenCodeAdapter(),
    "qodercli": QoderAdapter(),
    "agy": AgyAdapter(),
    "pi": PiAdapter(),
}

_ALIASES: Dict[str, str] = {
    "qoder": "qodercli",
    "qodercn": "qodercli",
}


def register_agent_adapter(adapter: AgentAdapter) -> None:
    """Register or override an AgentAdapter in the global registry."""
    _ADAPTER_REGISTRY[adapter.name] = adapter


def get_agent_adapter(agent_name: Optional[str] = None) -> AgentAdapter:
    """Get the appropriate AgentAdapter for a given agent name.
    
    Resolves aliases and falls back to TTYSteeringPrototypeAdapter if unspecified
    or unknown.
    """
    if not agent_name:
        return _ADAPTER_REGISTRY["tty_prototype"]

    clean_name = str(agent_name).strip().lower()
    resolved_name = _ALIASES.get(clean_name, clean_name)

    return _ADAPTER_REGISTRY.get(resolved_name, _ADAPTER_REGISTRY["tty_prototype"])


def list_agent_adapters() -> Dict[str, Dict[str, Any]]:
    """List all registered adapters and their capabilities."""
    return {name: adapter.to_dict() for name, adapter in _ADAPTER_REGISTRY.items()}
