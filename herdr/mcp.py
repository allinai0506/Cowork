#!/usr/bin/env python3
"""Herdr MCP Plugin & Capability Mesh (herdr/mcp.py).

Provides declarative MCP (Model Context Protocol) plugin management,
sandboxed capability binding, and permission enforcement for workflow nodes.
"""

import json
import os
from pathlib import Path
from typing import Any, Dict, List, Optional, Set

HOME = Path.home()
ROOT = HOME / ".herdr-controller"
MCP_FILE = Path(os.environ.get("MCP_FILE") or (ROOT / "mcp.json"))

BUNDLED_MCP_SERVERS = {
    "web_search": {
        "name": "web_search",
        "label": "实时网络检索与公开数据抓取",
        "capabilities": ["web_search", "research"],
        "permissions": ["read_only"],
        "description": "提供搜索引擎查询与公开网页内容抽取工具包",
        "bundled": True,
    },
    "data_extraction": {
        "name": "data_extraction",
        "label": "数据分析与财报指标提炼",
        "capabilities": ["data_extraction", "financial_excel_calc", "analysis"],
        "permissions": ["read_only"],
        "description": "解析结构化财报表格、计算核心比率指标与多维透视分析",
        "bundled": True,
    },
    "file_system": {
        "name": "file_system",
        "label": "工作区文件读写引擎",
        "capabilities": ["file_io", "code", "document_generation"],
        "permissions": ["read_write"],
        "description": "受限工作区内文件的创建、编辑与格式校验",
        "bundled": True,
    },
    "git_tools": {
        "name": "git_tools",
        "label": "Git 提交与分支版本控制",
        "capabilities": ["git_ops", "version_control"],
        "permissions": ["read_write"],
        "description": "工位独立 CoW 副本的提交、打标签与分支管理",
        "bundled": True,
    },
    "human_signoff": {
        "name": "human_signoff",
        "label": "合规与决策会签通道",
        "capabilities": ["signoff", "approval", "compliance"],
        "permissions": ["require_approval"],
        "description": "将高风险操作或最终交付成果提交人类总指挥确认会签",
        "bundled": True,
    },
}


def _get_mcp_file() -> Path:
    p = os.environ.get("MCP_FILE")
    if p:
        return Path(p)
    return MCP_FILE


def load_mcp_registry() -> Dict[str, Any]:
    """Load MCP server registry, populating bundled defaults if not present."""
    f = _get_mcp_file()
    registry: Dict[str, Any] = {"servers": dict(BUNDLED_MCP_SERVERS)}
    if f.exists():
        try:
            user_data = json.loads(f.read_text(encoding="utf-8"))
            if isinstance(user_data, dict) and "servers" in user_data:
                registry["servers"].update(user_data["servers"])
        except Exception:
            pass
    return registry


def save_mcp_registry(data: Dict[str, Any]) -> None:
    """Save user MCP server registry atomically."""
    f = _get_mcp_file()
    f.parent.mkdir(parents=True, exist_ok=True)
    tmp = f.with_suffix(f.suffix + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    tmp.replace(f)


def list_mcp_servers() -> List[Dict[str, Any]]:
    """List all registered MCP servers."""
    registry = load_mcp_registry()
    return list(registry.get("servers", {}).values())


def get_mcp_server(name: str) -> Optional[Dict[str, Any]]:
    """Get a registered MCP server by name."""
    registry = load_mcp_registry()
    return registry.get("servers", {}).get(name)


def register_mcp_server(name: str, config: Dict[str, Any]) -> Dict[str, Any]:
    """Register or update an MCP server."""
    if not name or not isinstance(name, str):
        raise ValueError("Server name must be a non-empty string.")

    registry = load_mcp_registry()
    entry = dict(config)
    entry["name"] = name
    entry["label"] = entry.get("label") or name
    entry["capabilities"] = list(entry.get("capabilities") or [])
    entry["permissions"] = list(entry.get("permissions") or ["read_only"])
    entry["bundled"] = bool(entry.get("bundled", False))

    registry["servers"][name] = entry
    save_mcp_registry(registry)
    return entry


def unregister_mcp_server(name: str) -> bool:
    """Unregister a custom MCP server (bundled servers cannot be deleted)."""
    registry = load_mcp_registry()
    server = registry.get("servers", {}).get(name)
    if not server:
        return False
    if server.get("bundled"):
        raise ValueError(f"Cannot unregister bundled MCP server '{name}'.")

    del registry["servers"][name]
    save_mcp_registry(registry)
    return True


def resolve_node_mcp(node: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Resolve and mount appropriate MCP servers for a node based on declared capabilities and permissions.
    
    A server is mounted if:
    1. It provides at least one capability declared in `worker_policy.capabilities` or `capabilities`.
    2. Its required permissions are permitted by the node's declared `worker_policy.permissions`.
    """
    worker_policy = node.get("worker_policy") or node.get("agent_policy") or {}
    declared_caps = set(
        worker_policy.get("capabilities")
        or node.get("capabilities")
        or []
    )
    declared_perms = set(
        worker_policy.get("permissions")
        or node.get("permissions")
        or ["read_only", "read_write"]
    )

    all_servers = list_mcp_servers()
    mounted: List[Dict[str, Any]] = []

    for server in all_servers:
        s_caps = set(server.get("capabilities", []))
        s_perms = set(server.get("permissions", ["read_only"]))

        # Check capability intersection
        if not (declared_caps & s_caps):
            continue

        # Check permission compatibility:
        # If server requires read_write or require_approval, node must grant it
        if "require_approval" in s_perms and "require_approval" not in declared_perms and "admin" not in declared_perms:
            continue
        if "read_write" in s_perms and "read_write" not in declared_perms and "admin" not in declared_perms:
            continue

        mounted.append(server)

    return mounted


def check_node_permissions(node: Dict[str, Any], requested_action: str) -> bool:
    """Validate whether a node's permission policy allows a specific action.
    
    Supported actions:
    - 'read': allowed by read_only, read_write, admin
    - 'write': allowed by read_write, admin
    - 'execute': allowed by read_write, admin
    - 'signoff': allowed by require_approval, admin
    """
    worker_policy = node.get("worker_policy") or node.get("agent_policy") or {}
    perms = set(
        worker_policy.get("permissions")
        or node.get("permissions")
        or ["read_only"]
    )

    if "admin" in perms:
        return True

    if requested_action in {"read", "query", "search"}:
        return any(p in perms for p in {"read_only", "read_write"})

    if requested_action in {"write", "edit", "commit", "execute"}:
        return "read_write" in perms

    if requested_action in {"signoff", "approval", "finalize"}:
        return "require_approval" in perms

    return False
