"""Dagster SEC pipeline jobs and schedules.

Pipeline Architecture (3 phases, run independently):

  Phase 1 - Download (EFTS-based, quarterly partitions):
    sec_download_job: sec_raw_filings
    Uses SEC EFTS API to discover and download XBRL ZIPs to S3.
    Quarterly partitions (e.g., 2024-Q1) to stay under EFTS 10k result limit.

  Phase 2 - Process (sensor-triggered or manual):
    sec_process_job: sec_process_filing (dynamic partitions)
    Parallel processing - one partition per filing.

  Phase 3 - Materialize:
    sec_materialize_job: sec_graph_materialized
    Discovers processed files, stages to DuckDB, ingests to LadybugDB graph.
    Uses SSE for progress monitoring on long-running operations.

Workflow:
  just sec-download 10 2024    # Download top 10 companies (all 4 quarters)
  just sec-process 2024        # Process in parallel
  just sec-materialize         # Ingest to graph

  # Or all-in-one for demos:
  just sec-load NVDA 2024      # Chains all steps for single company
"""

from datetime import UTC, datetime

from dagster import (
  AssetSelection,
  DefaultScheduleStatus,
  RunConfig,
  RunRequest,
  ScheduleDefinition,
  define_asset_job,
  schedule,
)

from robosystems.config import env
from robosystems.dagster.assets import (
  SECDownloadConfig,
  sec_duckdb_staged,
  sec_filing_partitions,
  sec_graph_from_duckdb,
  sec_graph_materialized,
  sec_process_filing,
  sec_quarter_partitions,
  sec_raw_filings,
)

# ============================================================================
# SEC Pipeline Jobs
# ============================================================================


# Phase 1: Download (quarter-partitioned)
# Downloads raw XBRL ZIPs to S3 using EFTS discovery.
# Uses quarterly partitions to stay under EFTS 10k result limit.
# Use with sec_processing_sensor to trigger parallel processing.
sec_download_job = define_asset_job(
  name="sec_download",
  description="Download SEC XBRL filings to S3 via EFTS (quarterly partitions).",
  selection=AssetSelection.assets(
    sec_raw_filings,
  ),
  tags={"pipeline": "sec", "phase": "download"},
  partitions_def=sec_quarter_partitions,
)


# Phase 2: Process (dynamic partitions per filing)
# NOTE: This job only includes sec_process_filing. Discovery is done by
# the sec_processing_sensor which registers partitions and triggers runs.
#
# Uses Fargate Spot (100% Spot, On-Demand fallback only if unavailable):
# - Short-running jobs (1-5 min) minimize interruption risk
# - Retry policy on asset handles Spot interruptions
# - Sensor re-triggers failed partitions automatically
# - ~70% cost savings at scale (10,000+ filings)
sec_process_job = define_asset_job(
  name="sec_process",
  description="Process SEC filings to parquet. One partition per filing.",
  selection=AssetSelection.assets(
    sec_process_filing,
  ),
  tags={
    "pipeline": "sec",
    "phase": "process",
    # Low priority (-1) so other jobs run first when queue is full
    "dagster/priority": "-1",
    # ECS Spot capacity provider override
    # 99% Spot with 1% On-Demand fallback for guaranteed availability
    "ecs/run_task_kwargs": {
      "capacityProviderStrategy": [
        {"capacityProvider": "FARGATE_SPOT", "weight": 99, "base": 0},
        {"capacityProvider": "FARGATE", "weight": 1, "base": 0},
      ],
    },
  },
  partitions_def=sec_filing_partitions,
)


# Phase 3: Materialize (unpartitioned)
sec_materialize_job = define_asset_job(
  name="sec_materialize",
  description="Materialize SEC graph from processed parquet files via DuckDB staging.",
  selection=AssetSelection.assets(sec_graph_materialized),
  tags={"pipeline": "sec", "phase": "materialize"},
)


# ============================================================================
# Decoupled Staging/Materialization Jobs (P0 Implementation)
# ============================================================================
# These jobs enable independent retry of staging and materialization:
# - If LadybugDB materialization fails, don't lose 2+ hours of DuckDB staging work
# - sec_stage_job: Stage to persistent DuckDB (can run independently)
# - sec_materialize_from_duckdb_job: Materialize from existing DuckDB

# Stage 1: DuckDB Staging (decoupled)
# Discovers processed files from S3 and stages to persistent DuckDB.
# Use when you want to stage without immediately materializing.
sec_stage_job = define_asset_job(
  name="sec_stage",
  description="Stage SEC files to persistent DuckDB (no graph ingestion).",
  selection=AssetSelection.assets(sec_duckdb_staged),
  tags={"pipeline": "sec", "phase": "stage", "decoupled": "true"},
)

# Stage 2: Materialization from DuckDB (decoupled)
# Materializes to LadybugDB from existing DuckDB staging.
# Use to retry materialization after a failure without re-staging.
sec_materialize_from_duckdb_job = define_asset_job(
  name="sec_materialize_from_duckdb",
  description="Materialize graph from existing DuckDB staging (retry-friendly).",
  selection=AssetSelection.assets(sec_graph_from_duckdb),
  tags={"pipeline": "sec", "phase": "materialize", "decoupled": "true"},
)

# Combined decoupled job: Run both stages in sequence
# Useful for full rebuilds with checkpointing between stages.
sec_staged_materialize_job = define_asset_job(
  name="sec_staged_materialize",
  description="Full SEC pipeline with persistent DuckDB staging (stage → materialize).",
  selection=AssetSelection.assets(sec_duckdb_staged, sec_graph_from_duckdb),
  tags={"pipeline": "sec", "phase": "full", "decoupled": "true"},
)


# ============================================================================
# SEC Pipeline Schedules
# ============================================================================

# Download schedule: Enable via SEC_DOWNLOAD_SCHEDULE_ENABLED=true
# Fetches new filings daily. Sensor auto-triggers parallel processing.
SEC_DOWNLOAD_SCHEDULE_STATUS = (
  DefaultScheduleStatus.RUNNING
  if env.SEC_DOWNLOAD_SCHEDULE_ENABLED
  else DefaultScheduleStatus.STOPPED
)

# Materialize schedule: Enable via SEC_MATERIALIZE_SCHEDULE_ENABLED=true
# OFF by default - run manually until comfortable with the pipeline.
SEC_MATERIALIZE_SCHEDULE_STATUS = (
  DefaultScheduleStatus.RUNNING
  if env.SEC_MATERIALIZE_SCHEDULE_ENABLED
  else DefaultScheduleStatus.STOPPED
)


def _get_quarters_to_scan() -> list[str]:
  """Get quarters to scan for daily download.

  Always scans current quarter. Also scans previous quarter during the first
  few days of a new quarter to catch late-indexed filings (filings submitted
  on the last day of a quarter may not appear in EFTS until the next day).

  Returns:
      List of partition keys like ["2025-Q1"] or ["2025-Q1", "2024-Q4"]
  """
  now = datetime.now(UTC)
  current_quarter = (now.month - 1) // 3 + 1
  current_year = now.year

  quarters = [f"{current_year}-Q{current_quarter}"]

  # Quarter start months: Q1=Jan, Q2=Apr, Q3=Jul, Q4=Oct
  quarter_start_month = (current_quarter - 1) * 3 + 1

  # Scan previous quarter for first 3 days of new quarter
  # This catches filings submitted late on quarter-end that get indexed next day
  if now.month == quarter_start_month and now.day <= 3:
    if current_quarter == 1:
      quarters.append(f"{current_year - 1}-Q4")
    else:
      quarters.append(f"{current_year}-Q{current_quarter - 1}")

  return quarters


@schedule(
  job=sec_download_job,
  cron_schedule="0 6 * * *",
  default_status=SEC_DOWNLOAD_SCHEDULE_STATUS,
)
def sec_daily_download_schedule(context):
  """Daily SEC download at 6 AM UTC via EFTS.

  Scans current quarter + previous quarter to catch late filings
  at quarter boundaries. Sensor triggers parallel processing.
  """
  quarters = _get_quarters_to_scan()
  context.log.info(f"Scheduling SEC download for quarters: {quarters}")

  for partition_key in quarters:
    yield RunRequest(
      run_key=f"sec-download-{partition_key}-{context.scheduled_execution_time.strftime('%Y%m%d')}",
      partition_key=partition_key,
      run_config=RunConfig(
        ops={
          "sec_raw_filings": SECDownloadConfig(
            skip_existing=True,
            form_types=["10-K", "10-Q"],
          ),
        }
      ),
    )


sec_nightly_materialize_schedule = ScheduleDefinition(
  name="sec_nightly_materialize",
  description="Nightly SEC graph materialization at 2 AM UTC. OFF by default.",
  job=sec_materialize_job,
  cron_schedule="0 2 * * *",
  default_status=SEC_MATERIALIZE_SCHEDULE_STATUS,
)
