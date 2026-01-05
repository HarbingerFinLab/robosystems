"""Observable source assets for direct graph operations.

These asset definitions allow AssetMaterializations reported from the API
(via direct execution) to appear in the Dagster UI's Assets tab.

Direct graph operations bypass Dagster job orchestration for performance
(eliminating 30-60s ECS cold start), but report materializations for
observability.
"""

from dagster import AssetKey, SourceAsset

# Observable source asset for graph creation
# Materializations are reported from direct_monitor.py
graphs_source = SourceAsset(
  key=AssetKey("graphs"),
  description=(
    "Graph databases created directly via the API. "
    "These operations bypass Dagster orchestration for performance but "
    "report materializations here for observability."
  ),
  group_name="graphs",
)

# Observable source asset for graph provisioning (post-payment)
graph_provisioning_source = SourceAsset(
  key=AssetKey("graph_provisioning"),
  description=(
    "Graph provisioning after payment confirmation. "
    "These operations bypass Dagster orchestration for faster "
    "post-payment provisioning."
  ),
  group_name="graphs",
)

# Observable source asset for repository provisioning
repository_provisioning_source = SourceAsset(
  key=AssetKey("repository_provisioning"),
  description=(
    "Repository access provisioning after payment confirmation. "
    "Grants access to shared repositories (SEC, industry, economic)."
  ),
  group_name="graphs",
)

# Observable source asset for subgraph creation
subgraphs_source = SourceAsset(
  key=AssetKey("subgraphs"),
  description=(
    "Subgraphs created from parent graphs. "
    "These operations bypass Dagster orchestration for performance."
  ),
  group_name="graphs",
)
