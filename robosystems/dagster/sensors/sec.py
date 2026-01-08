"""SEC pipeline sensors for parallel processing.

This sensor watches for raw XBRL filings in S3 and triggers parallel
processing jobs for each unprocessed filing.

Architecture:
- Phase 1 (Downloads): sec_raw_filings downloads ZIPs to S3 (2 concurrent max)
- Phase 2 (Processing): This sensor triggers sec_process_job for each filing (20 concurrent max)
- Phase 3 (Materialization): sec_materialize_job ingests to graph (sequential)

The sensor polls S3 every 60 seconds, finds raw ZIPs without corresponding
parquet output, registers dynamic partitions, and triggers parallel processing.
"""

import boto3
from botocore.exceptions import ClientError
from dagster import (
  DefaultSensorStatus,
  RunRequest,
  SensorEvaluationContext,
  SkipReason,
  sensor,
)

from robosystems.config import env
from robosystems.config.storage.shared import (
  DataSourceType,
  get_processed_key,
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
  (matches structure from get_raw_key(DataSourceType.SEC, ...))

  Returns:
      Tuple of (year, cik, accession) or None if invalid format
  """
  # Get the expected prefix for SEC data
  sec_prefix = get_raw_key(DataSourceType.SEC)  # Returns "sec"

  parts = key.split("/")
  if len(parts) < 4 or not parts[-1].endswith(".zip"):
    return None

  # First part should match SEC prefix
  if parts[0] != sec_prefix:
    return None

  # Extract year from "year=2024"
  year_part = parts[1]
  if not year_part.startswith("year="):
    return None
  year = year_part.replace("year=", "")

  # CIK is the third part
  cik = parts[2]

  # Accession is filename without .zip
  accession = parts[-1].replace(".zip", "")

  return year, cik, accession


# Sensor status controlled by environment variable
SEC_PARALLEL_SENSOR_STATUS = (
  DefaultSensorStatus.RUNNING
  if env.SEC_PARALLEL_SENSOR_ENABLED
  else DefaultSensorStatus.STOPPED
)


# Maximum files to process per sensor tick to avoid timeout
MAX_FILES_PER_TICK = 500


def _list_processed_partitions(s3_client, bucket: str) -> set[str]:
  """List all processed partition keys from S3.

  Returns set of partition keys in format: {year}_{cik}_{accession}
  This is much faster than individual HEAD requests.
  """
  processed = set()
  paginator = s3_client.get_paginator("list_objects_v2")

  # List Entity parquet files as proxy for "fully processed"
  # Format: sec/year=2024/nodes/Entity/{cik}_{accession}.parquet
  processed_prefix = f"{get_processed_key(DataSourceType.SEC)}/"

  for page in paginator.paginate(Bucket=bucket, Prefix=processed_prefix):
    for obj in page.get("Contents", []):
      key = obj["Key"]
      if "/nodes/Entity/" in key and key.endswith(".parquet"):
        # Extract partition key from filename: {cik}_{accession}.parquet
        filename = key.split("/")[-1].replace(".parquet", "")
        # Extract year from path: sec/year=2024/nodes/Entity/...
        parts = key.split("/")
        for part in parts:
          if part.startswith("year="):
            year = part.replace("year=", "")
            partition_key = f"{year}_{filename}"
            processed.add(partition_key)
            break

  return processed


@sensor(
  job=sec_process_job,
  minimum_interval_seconds=60,
  default_status=SEC_PARALLEL_SENSOR_STATUS,
  description="Watch for raw SEC filings and trigger parallel processing",
)
def sec_processing_sensor(context: SensorEvaluationContext):
  """Watch for raw SEC filings in S3 and trigger parallel processing.

  This sensor:
  1. Lists raw XBRL ZIPs in S3 (sec/year=*/cik/*.zip) in batches
  2. Compares against processed files (set comparison, not individual HEAD requests)
  3. Registers dynamic partitions for unprocessed filings
  4. Yields RunRequest for each to trigger sec_process_job
  5. Uses cursor to track progress and resume across ticks

  The QueuedRunCoordinator limits concurrent runs (default 20).

  Partition key format: {year}_{cik}_{accession}
  """
  # Skip in dev environment to avoid S3 connection issues
  if env.ENVIRONMENT == "dev":
    yield SkipReason(
      "Skipped in dev environment - use sec-process-parallel for local testing"
    )
    return

  raw_bucket = env.SHARED_RAW_BUCKET
  processed_bucket = env.SHARED_PROCESSED_BUCKET

  # Validate required S3 bucket configuration
  if not raw_bucket or not processed_bucket:
    yield SkipReason(
      "Missing required S3 bucket configuration (SHARED_RAW_BUCKET or SHARED_PROCESSED_BUCKET)"
    )
    return

  s3_client = _get_s3_client()

  try:
    # Get cursor for pagination (last processed S3 key)
    start_after = context.cursor or ""

    # List raw ZIPs in batches using cursor
    paginator = s3_client.get_paginator("list_objects_v2")
    raw_files = []

    sec_prefix = f"{get_raw_key(DataSourceType.SEC)}/"  # "sec/"

    paginate_kwargs = {"Bucket": raw_bucket, "Prefix": sec_prefix}
    if start_after:
      paginate_kwargs["StartAfter"] = start_after

    for page in paginator.paginate(**paginate_kwargs):
      for obj in page.get("Contents", []):
        key = obj["Key"]
        if key.endswith(".zip"):
          raw_files.append(key)
          if len(raw_files) >= MAX_FILES_PER_TICK:
            break
      if len(raw_files) >= MAX_FILES_PER_TICK:
        break

    if not raw_files:
      # No more files to process - reset cursor to start fresh next time
      if start_after:
        context.log.info("Completed full scan, resetting cursor")
        context.update_cursor("")
        yield SkipReason("Completed full scan of raw filings, cursor reset")
      else:
        yield SkipReason("No raw filings found in S3")
      return

    context.log.info(
      f"Processing batch of {len(raw_files)} raw filings "
      f"(cursor: {start_after[:50] + '...' if start_after else 'start'})"
    )

    # Build set of already-processed partitions (single list operation, much faster)
    processed_partitions = _list_processed_partitions(s3_client, processed_bucket)
    context.log.info(f"Found {len(processed_partitions)} already-processed filings")

    # Track new partitions to register
    new_partitions = []
    run_requests = []
    last_key = ""

    for raw_key in raw_files:
      last_key = raw_key
      parsed = _parse_raw_s3_key(raw_key)
      if not parsed:
        continue

      year, cik, accession = parsed
      partition_key = f"{year}_{cik}_{accession}"

      # Check if already processed using set lookup (O(1))
      if partition_key in processed_partitions:
        continue

      # Add to new partitions list
      new_partitions.append(partition_key)

      # Create run request with idempotent run_key
      run_requests.append(
        RunRequest(
          run_key=f"sec-process-{partition_key}",
          partition_key=partition_key,
        )
      )

    # Update cursor to last processed key for next tick
    if last_key:
      context.update_cursor(last_key)

    if not new_partitions:
      context.log.info("All filings in batch already processed")
      yield SkipReason("All filings in batch already processed")
      return

    # Register dynamic partitions in batch
    context.log.info(f"Registering {len(new_partitions)} dynamic partitions")
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
    # Re-raise to mark sensor run as failed - Dagster will retry
    raise
  except Exception as e:
    context.log.error(f"Error in SEC processing sensor: {type(e).__name__}: {e}")
    # Re-raise to mark sensor run as failed - Dagster will retry
    raise
