"""External asset spec for user graph staged files.

This asset definition allows AssetMaterializations reported from the API
(via direct staging) to appear in the Dagster UI's Assets tab.

Direct staging bypasses Dagster job orchestration for performance, but
reports materializations for observability. This asset definition makes
those materializations visible in the UI.
"""

from dagster import AssetSpec

# External asset for user graph staged files
# This matches the asset key used in direct_staging.py
# Since graph_ids are dynamic, we define a base asset that receives all materializations
user_graph_file_staging_source = AssetSpec(
  key="user_graph_file_staging",
  description=(
    "User files staged directly to DuckDB via the API. "
    "These files bypass Dagster orchestration for performance but "
    "report materializations here for observability."
  ),
  group_name="graphs",
  metadata={
    "pipeline": "graphs",
    "stage": "staging",
  },
  kinds={"duckdb"},
)
