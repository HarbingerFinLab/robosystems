"""SEC pipeline sensors for parallel processing.

This sensor watches for raw XBRL filings in S3 and triggers parallel
processing jobs for each unprocessed filing.

Architecture:
- Phase 1 (Downloads): sec_raw_filings downloads ZIPs to S3 (quarterly partitions)
- Phase 2 (Processing): This sensor triggers sec_process_job for each filing
- Phase 3 (Materialization): sec_materialize_job ingests to graph (sequential)

The sensor scans S3 year-by-year (newest first), comparing against Dagster's
materialization tracking to find unprocessed filings.
"""

import json
from datetime import UTC, datetime

import boto3
from botocore.exceptions import ClientError
from dagster import (
  AssetKey,
  DefaultSensorStatus,
  RunRequest,
  SensorEvaluationContext,
  SkipReason,
  sensor,
)

from robosystems.config import env
from robosystems.config.storage.shared import (
  DataSourceType,
  get_raw_key,
)
from robosystems.dagster.jobs.sec import sec_process_job


def _get_s3_client():
  """Create S3 client with LocalStack support for dev."""
  kwargs = {
    "region_name": env.AWS_REGION or "us-east-1",
  }
  if env.AWS_ENDPOINT_URL:
    kwargs["endpoint_url"] = env.AWS_ENDPOINT_URL
  return boto3.client("s3", **kwargs)


def _parse_raw_s3_key(key: str) -> tuple[str, str, str] | None:
  """Parse S3 key to extract year, cik, accession.

  Expected format: sec/year=2024/320193/0000320193-24-000081.zip

  Returns:
      Tuple of (year, cik, accession) or None if invalid format
  """
  sec_prefix = get_raw_key(DataSourceType.SEC)  # Returns "sec"

  parts = key.split("/")
  if len(parts) < 4 or not parts[-1].endswith(".zip"):
    return None

  if parts[0] != sec_prefix:
    return None

  year_part = parts[1]
  if not year_part.startswith("year="):
    return None
  year = year_part.replace("year=", "")

  cik = parts[2]
  accession = parts[-1].replace(".zip", "")

  return year, cik, accession


# Sensor status controlled by environment variable
SEC_PARALLEL_SENSOR_STATUS = (
  DefaultSensorStatus.RUNNING
  if env.SEC_PARALLEL_SENSOR_ENABLED
  else DefaultSensorStatus.STOPPED
)

# Configuration
# Batch size per tick - balances efficiency with memory usage and DB transaction size.
# Can be high since we use Dagster DB for materialization tracking (O(1) lookups).
MAX_FILES_PER_TICK = 500

# Start year for SEC data loading (XBRL filings began 2009)
SEC_START_YEAR = 2009


def _get_years_to_scan() -> list[str]:
  """Get list of years to scan, newest first.

  Dynamically calculates range from SEC_START_YEAR to current year.
  Aligned with SEC_QUARTERS in assets/sec.py.
  """
  current_year = datetime.now(UTC).year
  return [str(y) for y in range(current_year, SEC_START_YEAR - 1, -1)]


def _list_raw_files_for_year(
  s3_client, bucket: str, year: str, start_after: str | None = None
) -> list[str]:
  """List raw ZIP files for a specific year.

  Args:
      s3_client: boto3 S3 client
      bucket: S3 bucket name
      year: Year to scan (e.g., "2025")
      start_after: Optional S3 key to start after (for pagination)

  Returns:
      List of S3 keys for raw ZIP files
  """
  paginator = s3_client.get_paginator("list_objects_v2")
  year_prefix = f"{get_raw_key(DataSourceType.SEC)}/year={year}/"

  paginate_kwargs = {"Bucket": bucket, "Prefix": year_prefix}
  # Only resume pagination if cursor is from the same year; otherwise start fresh
  if start_after and start_after.startswith(year_prefix):
    paginate_kwargs["StartAfter"] = start_after

  raw_files = []
  for page in paginator.paginate(**paginate_kwargs):
    for obj in page.get("Contents", []):
      key = obj["Key"]
      if key.endswith(".zip"):
        raw_files.append(key)
        if len(raw_files) >= MAX_FILES_PER_TICK:
          return raw_files

  return raw_files


@sensor(
  job=sec_process_job,
  minimum_interval_seconds=60,
  default_status=SEC_PARALLEL_SENSOR_STATUS,
  description="Watch for raw SEC filings and trigger parallel processing",
)
def sec_processing_sensor(context: SensorEvaluationContext):
  """Watch for raw SEC filings in S3 and trigger parallel processing.

  Scans year-by-year (newest first: 2026 -> 2025 -> ... -> 2009).
  Within each year, paginates through files using cursor.
  When all years exhausted, resets and starts over to catch new files.

  Cursor format (JSON):
  {
    "year": "2025",      # Current year being scanned
    "last_key": "..."    # Last S3 key processed (for pagination within year)
  }
  """
  if env.ENVIRONMENT == "dev":
    yield SkipReason(
      "Skipped in dev environment - use sec-process-parallel for local testing"
    )
    return

  raw_bucket = env.SHARED_RAW_BUCKET

  if not raw_bucket:
    yield SkipReason("Missing required S3 bucket configuration (SHARED_RAW_BUCKET)")
    return

  s3_client = _get_s3_client()

  try:
    # Parse cursor
    cursor_data: dict[str, str | None] = {"year": None, "last_key": None}
    if context.cursor:
      try:
        cursor_data = json.loads(context.cursor)
      except json.JSONDecodeError:
        context.log.info("Resetting invalid cursor format")

    # Determine which year to scan
    years = _get_years_to_scan()
    current_scan_year = cursor_data.get("year")
    last_key = cursor_data.get("last_key")

    if current_scan_year is None or current_scan_year not in years:
      current_scan_year = years[0]  # Start with newest year
      last_key = None

    context.log.info(
      f"Scanning year={current_scan_year}, "
      f"cursor={last_key[:50] + '...' if last_key else 'start'}"
    )

    # List raw files for current year
    raw_files = _list_raw_files_for_year(
      s3_client, raw_bucket, current_scan_year, last_key
    )

    # If no more files in current year, move to next year
    if not raw_files:
      year_idx = years.index(current_scan_year)
      if year_idx < len(years) - 1:
        # Move to next year
        next_year = years[year_idx + 1]
        context.log.info(f"Completed year {current_scan_year}, moving to {next_year}")
        context.update_cursor(json.dumps({"year": next_year, "last_key": None}))
        yield SkipReason(f"Completed year {current_scan_year}, continuing with {next_year}")
      else:
        # Completed all years - reset to start fresh
        context.log.info(f"Completed full scan of years {years[0]} to {years[-1]}, restarting")
        context.update_cursor(json.dumps({"year": None, "last_key": None}))
        yield SkipReason("Completed full scan, restarting from newest year")
      return

    context.log.info(f"Found {len(raw_files)} raw files in year {current_scan_year}")

    # Get materialized partitions from Dagster (fast DB query)
    materialized_partitions = context.instance.get_materialized_partitions(
      AssetKey("sec_process_filing")
    )
    context.log.info(f"Total materialized partitions: {len(materialized_partitions)}")

    # Find unprocessed files
    new_partitions = []
    run_requests = []
    last_processed_key = None

    for raw_key in raw_files:
      last_processed_key = raw_key
      parsed = _parse_raw_s3_key(raw_key)
      if not parsed:
        continue

      year, cik, accession = parsed
      partition_key = f"{year}_{cik}_{accession}"

      # Check if already materialized (O(1) set lookup)
      if partition_key in materialized_partitions:
        continue

      new_partitions.append(partition_key)
      run_requests.append(
        RunRequest(
          run_key=f"sec-process-{partition_key}",
          partition_key=partition_key,
        )
      )

    # Update cursor to continue from last file
    context.update_cursor(
      json.dumps({"year": current_scan_year, "last_key": last_processed_key})
    )

    if not new_partitions:
      context.log.info("All filings in batch already materialized")
      yield SkipReason("All filings in batch already materialized")
      return

    # Register dynamic partitions in batch
    context.log.info(f"Registering {len(new_partitions)} new partitions")
    context.instance.add_dynamic_partitions(
      partitions_def_name="sec_filings",
      partition_keys=new_partitions,
    )

    # Yield run requests
    context.log.info(f"Triggering {len(run_requests)} processing jobs")
    yield from run_requests

  except ClientError as e:
    error_code = e.response.get("Error", {}).get("Code", "Unknown")
    if error_code == "NoSuchBucket":
      context.log.error(f"S3 bucket does not exist: {raw_bucket}")
    elif error_code == "AccessDenied":
      context.log.error(f"Access denied to S3 bucket: {raw_bucket}")
    else:
      context.log.error(f"S3 error ({error_code}): {e}")
    raise
  except Exception as e:
    context.log.error(f"Error in SEC processing sensor: {type(e).__name__}: {e}")
    raise
