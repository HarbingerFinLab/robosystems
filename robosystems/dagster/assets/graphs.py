"""Observable source assets for direct graph operations.

These asset definitions allow AssetMaterializations reported from the API
(via direct execution) to appear in the Dagster UI's Assets tab.

Direct graph operations bypass Dagster job orchestration for performance
(eliminating 30-60s ECS cold start), but report materializations for
observability.
"""

from dagster import AssetKey, SourceAsset

# Observable source asset for user graph creation
# Materializations are reported from direct_monitor.py
user_graph_creation_source = SourceAsset(
  key=AssetKey("user_graph_creation"),
  description=(
    "User graph databases created directly via the API. "
    "These operations bypass Dagster orchestration for performance but "
    "report materializations here for observability."
  ),
  group_name="graphs",
)

# Observable source asset for user graph provisioning (post-payment)
user_graph_provisioning_source = SourceAsset(
  key=AssetKey("user_graph_provisioning"),
  description=(
    "User graph provisioning after payment confirmation. "
    "These operations bypass Dagster orchestration for faster "
    "post-payment provisioning."
  ),
  group_name="graphs",
)

# Observable source asset for repository access provisioning
repository_access_provisioning_source = SourceAsset(
  key=AssetKey("repository_access_provisioning"),
  description=(
    "Repository access provisioning after payment confirmation. "
    "Grants access to shared repositories (SEC, industry, economic)."
  ),
  group_name="graphs",
)

# Observable source asset for user subgraph creation
user_subgraph_creation_source = SourceAsset(
  key=AssetKey("user_subgraph_creation"),
  description=(
    "User subgraphs created from parent graphs. "
    "These operations bypass Dagster orchestration for performance."
  ),
  group_name="graphs",
)
