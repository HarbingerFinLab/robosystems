"""Dagster assets for data pipelines.

Assets represent data artifacts that are produced and consumed:
- SEC pipeline assets (extraction, processing, materialization)
- QuickBooks pipeline assets (sync, transform, materialize)
- Plaid pipeline assets (sync, transform, materialize)
- Staged files (observable source for direct API staging)
"""

from robosystems.dagster.assets.graphs import (
  graph_provisioning_source,
  graphs_source,
  repository_provisioning_source,
  subgraphs_source,
)
from robosystems.dagster.assets.plaid import (
  plaid_accounts,
  plaid_graph_data,
  plaid_transactions,
)
from robosystems.dagster.assets.quickbooks import (
  qb_accounts,
  qb_graph_data,
  qb_transactions,
)
from robosystems.dagster.assets.sec import (
  # Config classes
  SECDownloadConfig,
  SECMaterializeConfig,
  SECSingleFilingConfig,
  SECStageConfig,
  # Assets - two-stage materialization
  sec_duckdb_staged,
  # Assets - dynamic partition processing
  sec_filing_partitions,
  sec_graph_materialized,
  sec_process_filing,
  # Partitions (quarterly to stay under EFTS 10k limit)
  sec_quarter_partitions,
  sec_raw_filings,
)
from robosystems.dagster.assets.staged_files import staged_files_source

__all__ = [
  "SECDownloadConfig",
  "SECMaterializeConfig",
  "SECSingleFilingConfig",
  "SECStageConfig",
  "graph_provisioning_source",
  "graphs_source",
  "plaid_accounts",
  "plaid_graph_data",
  "plaid_transactions",
  "qb_accounts",
  "qb_graph_data",
  "qb_transactions",
  "repository_provisioning_source",
  "sec_duckdb_staged",
  "sec_filing_partitions",
  "sec_graph_materialized",
  "sec_process_filing",
  "sec_quarter_partitions",
  "sec_raw_filings",
  "staged_files_source",
  "subgraphs_source",
]
