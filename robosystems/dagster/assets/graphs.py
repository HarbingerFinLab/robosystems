"""External asset specs for direct graph operations.

These asset definitions allow AssetMaterializations reported from the API
(via direct execution) to appear in the Dagster UI's Assets tab.

Direct graph operations bypass Dagster job orchestration for performance
(eliminating 30-60s ECS cold start), but report materializations for
observability.
"""

from dagster import AssetSpec

# External asset for user graph creation (unified)
# Materializations are reported from direct_monitor.py
# Tracks both direct API creation and subscription-based provisioning
# via the 'provisioning_method' metadata field ('direct' or 'subscription')
user_graph_creation_source = AssetSpec(
  key="user_graph_creation",
  description=(
    "User graph databases created via the API. "
    "Check 'provisioning_method' metadata for creation context."
  ),
  group_name="graphs",
  metadata={
    "pipeline": "graphs",
    "stage": "creation",
  },
  kinds={"provision"},
)

# External asset for user repository provisioning
user_repository_provisioning_source = AssetSpec(
  key="user_repository_provisioning",
  description=(
    "User access provisioned to shared repositories. "
    "Includes credit allocation and access grants."
  ),
  group_name="graphs",
  metadata={
    "pipeline": "graphs",
    "stage": "repository_provisioning",
  },
  kinds={"provision"},
)

# External asset for user subgraph creation
user_subgraph_creation_source = AssetSpec(
  key="user_subgraph_creation",
  description=(
    "User subgraphs created from parent graphs. "
    "These operations bypass Dagster orchestration for performance."
  ),
  group_name="graphs",
  metadata={
    "pipeline": "graphs",
    "stage": "subgraph_creation",
  },
  kinds={"provision"},
)
