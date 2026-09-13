"""Tests for dynamic workflow schema and validation (Phase 4).

Covers:
- Extended node contract: inputs, worker_policy, gate (with retry_target).
- Validation: invalid retry_target and invalid node input references.
- Backward compatibility with software-development-v1 and bidding.
- Loading of cross-domain business-research-v1 template.
"""

import pytest
from herdr import workflow


def test_bundled_templates_exist():
    templates = workflow.list_templates()
    assert "software-development-v1" in templates
    assert "bidding" in templates
    assert "business-research-v1" in templates


def test_load_business_research_template():
    tmpl = workflow.load_template("business-research-v1")
    assert tmpl["name"] == "business-research-v1"
    assert len(tmpl["nodes"]) == 4

    nodes_map = {n["id"]: n for n in tmpl["nodes"]}
    scope = nodes_map["market_scope"]
    assert scope["inputs"] == [{"ref": "context.user_goal"}]
    assert "research" in scope["worker_policy"]["capabilities"]
    assert "read_only" in scope["worker_policy"]["permissions"]

    briefing = nodes_map["executive_briefing"]
    assert briefing["node_type"] == "gate"
    assert briefing["gate"]["type"] == "hybrid"
    assert briefing["gate"]["requires_human_approval"] is True
    assert briefing["gate"]["retry_target"] == "data_extraction"


def test_validate_invalid_retry_target():
    invalid_nodes = [
        {
            "id": "step1",
            "label": "Step 1",
            "depends_on": [],
            "gate": {"retry_target": "non_existent_step"},
        }
    ]
    with pytest.raises(ValueError, match="gate retry_target references unknown node"):
        workflow.validate_workflow_dag(invalid_nodes)


def test_validate_invalid_input_node_reference():
    invalid_nodes = [
        {
            "id": "step1",
            "label": "Step 1",
            "depends_on": [],
            "inputs": [{"ref": "nodes.ghost_node.outputs"}],
        }
    ]
    with pytest.raises(ValueError, match="input references unknown node 'ghost_node'"):
        workflow.validate_workflow_dag(invalid_nodes)


def test_backward_compatibility_software_dev():
    tmpl = workflow.load_template("software-development-v1")
    assert tmpl["name"] == "software-development-v1"
    for n in tmpl["nodes"]:
        assert "inputs" in n
        assert "worker_policy" in n
        assert "gate" in n
