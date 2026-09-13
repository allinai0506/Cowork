"""Tests for MCP capability mesh and sandboxed permissions (herdr/mcp.py).

Covers:
- MCP server registry loading and bundled servers.
- Registration and unregistration of custom MCP servers.
- Prevention of unregistering bundled servers.
- Capability-based server resolution for nodes.
- Permission enforcement (read_only, read_write, require_approval).
"""

import json
import pytest
from pathlib import Path
from herdr import mcp


@pytest.fixture
def mcp_env(tmp_path, monkeypatch):
    mcp_file = tmp_path / "mcp.json"
    monkeypatch.setenv("MCP_FILE", str(mcp_file))
    return {"mcp_file": mcp_file}


def test_list_bundled_mcp_servers(mcp_env):
    servers = mcp.list_mcp_servers()
    names = {s["name"] for s in servers}
    assert "web_search" in names
    assert "data_extraction" in names
    assert "file_system" in names
    assert "git_tools" in names
    assert "human_signoff" in names


def test_register_and_unregister_custom_mcp(mcp_env):
    custom_cfg = {
        "label": "Custom Vector Database",
        "capabilities": ["vector_search", "similarity"],
        "permissions": ["read_only"],
    }
    entry = mcp.register_mcp_server("custom_vector", custom_cfg)
    assert entry["name"] == "custom_vector"
    assert entry["bundled"] is False

    loaded = mcp.get_mcp_server("custom_vector")
    assert loaded is not None
    assert "vector_search" in loaded["capabilities"]

    # Unregister
    assert mcp.unregister_mcp_server("custom_vector") is True
    assert mcp.get_mcp_server("custom_vector") is None


def test_cannot_unregister_bundled_mcp(mcp_env):
    with pytest.raises(ValueError, match="Cannot unregister bundled MCP server"):
        mcp.unregister_mcp_server("web_search")


def test_resolve_node_mcp_matching_capabilities(mcp_env):
    # Node declaring web_search capability and read_only permission
    node_research = {
        "id": "research",
        "worker_policy": {
            "capabilities": ["web_search"],
            "permissions": ["read_only"],
        },
    }
    mounted = mcp.resolve_node_mcp(node_research)
    mounted_names = {s["name"] for s in mounted}
    assert "web_search" in mounted_names
    assert "file_system" not in mounted_names  # requires read_write


def test_resolve_node_mcp_permission_boundary(mcp_env):
    # Node declaring file_io capability but only granted read_only permission
    node_restricted = {
        "id": "inspect",
        "worker_policy": {
            "capabilities": ["file_io"],
            "permissions": ["read_only"],  # restricted!
        },
    }
    mounted = mcp.resolve_node_mcp(node_restricted)
    # file_system requires read_write, so it should NOT be mounted
    assert not any(s["name"] == "file_system" for s in mounted)


def test_check_node_permissions():
    node_ro = {"worker_policy": {"permissions": ["read_only"]}}
    assert mcp.check_node_permissions(node_ro, "read") is True
    assert mcp.check_node_permissions(node_ro, "write") is False
    assert mcp.check_node_permissions(node_ro, "signoff") is False

    node_rw = {"worker_policy": {"permissions": ["read_write"]}}
    assert mcp.check_node_permissions(node_rw, "read") is True
    assert mcp.check_node_permissions(node_rw, "write") is True
    assert mcp.check_node_permissions(node_rw, "signoff") is False

    node_admin = {"worker_policy": {"permissions": ["admin"]}}
    assert mcp.check_node_permissions(node_admin, "read") is True
    assert mcp.check_node_permissions(node_admin, "write") is True
    assert mcp.check_node_permissions(node_admin, "signoff") is True
