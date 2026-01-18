"""Tests for SEC processing sensor."""

import json
from unittest.mock import MagicMock, patch

import pytest
from botocore.exceptions import ClientError
from dagster import build_sensor_context

from robosystems.dagster.sensors.sec import (
  _get_years_to_scan,
  _list_raw_files_for_year,
  _parse_raw_s3_key,
  sec_processing_sensor,
)


class TestParseRawS3Key:
  """Tests for _parse_raw_s3_key helper."""

  def test_valid_key(self):
    """Test parsing a valid S3 key."""
    key = "sec/year=2024/320193/0000320193-24-000081.zip"
    result = _parse_raw_s3_key(key)

    assert result is not None
    year, cik, accession = result
    assert year == "2024"
    assert cik == "320193"
    assert accession == "0000320193-24-000081"

  def test_invalid_key_no_zip(self):
    """Test parsing a key without .zip extension."""
    key = "sec/year=2024/320193/0000320193-24-000081.parquet"
    result = _parse_raw_s3_key(key)
    assert result is None

  def test_invalid_key_wrong_format(self):
    """Test parsing a key with wrong format."""
    key = "processed/year=2024/nodes/Entity.parquet"
    result = _parse_raw_s3_key(key)
    assert result is None

  def test_invalid_key_no_year_prefix(self):
    """Test parsing a key without year= prefix."""
    key = "sec/2024/320193/0000320193-24-000081.zip"
    result = _parse_raw_s3_key(key)
    assert result is None


class TestGetYearsToScan:
  """Tests for _get_years_to_scan helper."""

  def test_returns_years_newest_first(self):
    """Test that it returns years from current year back to 2009, newest first."""
    years = _get_years_to_scan()
    # Should start with current year and go back to 2009 (XBRL start)
    assert years[-1] == "2009"
    assert years[0] > years[-1]  # Newest first
    assert len(years) >= 17  # At least 2009-2025

  def test_years_are_contiguous(self):
    """Test that years form a contiguous range."""
    years = _get_years_to_scan()
    int_years = [int(y) for y in years]
    # Should be descending by 1 each step
    for i in range(len(int_years) - 1):
      assert int_years[i] - int_years[i + 1] == 1


class TestListRawFilesForYear:
  """Tests for _list_raw_files_for_year helper."""

  def test_lists_zip_files(self):
    """Test listing ZIP files for a year."""
    mock_client = MagicMock()
    mock_paginator = MagicMock()
    mock_paginator.paginate.return_value = [
      {
        "Contents": [
          {"Key": "sec/year=2024/320193/0000320193-24-000081.zip"},
          {"Key": "sec/year=2024/320193/0000320193-24-000082.zip"},
        ]
      }
    ]
    mock_client.get_paginator.return_value = mock_paginator

    result = _list_raw_files_for_year(mock_client, "test-bucket", "2024")

    assert len(result) == 2
    assert "sec/year=2024/320193/0000320193-24-000081.zip" in result

  def test_respects_max_files(self):
    """Test that MAX_FILES_PER_TICK is respected."""
    mock_client = MagicMock()
    mock_paginator = MagicMock()
    # Return more files than MAX_FILES_PER_TICK
    mock_paginator.paginate.return_value = [
      {
        "Contents": [
          {"Key": f"sec/year=2024/320193/0000320193-24-{i:06d}.zip"}
          for i in range(600)
        ]
      }
    ]
    mock_client.get_paginator.return_value = mock_paginator

    result = _list_raw_files_for_year(mock_client, "test-bucket", "2024")

    # Should be limited to MAX_FILES_PER_TICK (500)
    assert len(result) == 500

  def test_uses_start_after_when_in_same_year(self):
    """Test that start_after is used when key is in same year."""
    mock_client = MagicMock()
    mock_paginator = MagicMock()
    mock_paginator.paginate.return_value = [{"Contents": []}]
    mock_client.get_paginator.return_value = mock_paginator

    _list_raw_files_for_year(
      mock_client,
      "test-bucket",
      "2024",
      start_after="sec/year=2024/320193/0000320193-24-000081.zip",
    )

    # Verify StartAfter was passed
    call_kwargs = mock_paginator.paginate.call_args[1]
    assert "StartAfter" in call_kwargs

  def test_ignores_start_after_from_different_year(self):
    """Test that start_after is ignored when key is from different year."""
    mock_client = MagicMock()
    mock_paginator = MagicMock()
    mock_paginator.paginate.return_value = [{"Contents": []}]
    mock_client.get_paginator.return_value = mock_paginator

    _list_raw_files_for_year(
      mock_client,
      "test-bucket",
      "2024",
      start_after="sec/year=2023/320193/0000320193-23-000081.zip",  # Different year
    )

    # Verify StartAfter was NOT passed (key is from different year)
    call_kwargs = mock_paginator.paginate.call_args[1]
    assert "StartAfter" not in call_kwargs


class TestSecProcessingSensor:
  """Tests for sec_processing_sensor."""

  @patch("robosystems.dagster.sensors.sec.env")
  def test_skips_in_dev_environment(self, mock_env):
    """Test sensor skips in dev environment."""
    mock_env.ENVIRONMENT = "dev"

    context = build_sensor_context()
    result = list(sec_processing_sensor(context))

    assert len(result) == 1
    assert "Skipped in dev environment" in str(result[0])

  @patch("robosystems.dagster.sensors.sec.env")
  def test_skips_without_bucket_config(self, mock_env):
    """Test sensor skips when S3 buckets are not configured."""
    mock_env.ENVIRONMENT = "prod"
    mock_env.SHARED_RAW_BUCKET = None

    context = build_sensor_context()
    result = list(sec_processing_sensor(context))

    assert len(result) == 1
    assert "Missing required S3 bucket configuration" in str(result[0])

  @patch("robosystems.dagster.sensors.sec._get_s3_client")
  @patch("robosystems.dagster.sensors.sec.env")
  def test_handles_no_such_bucket_error(self, mock_env, mock_get_client):
    """Test sensor handles NoSuchBucket error gracefully."""
    mock_env.ENVIRONMENT = "prod"
    mock_env.SHARED_RAW_BUCKET = "nonexistent-bucket"

    mock_client = MagicMock()
    mock_paginator = MagicMock()
    mock_paginator.paginate.side_effect = ClientError(
      {"Error": {"Code": "NoSuchBucket", "Message": "Bucket does not exist"}},
      "ListObjectsV2",
    )
    mock_client.get_paginator.return_value = mock_paginator
    mock_get_client.return_value = mock_client

    context = build_sensor_context()

    with pytest.raises(ClientError):
      list(sec_processing_sensor(context))

  @patch("robosystems.dagster.sensors.sec._get_years_to_scan")
  @patch("robosystems.dagster.sensors.sec._get_s3_client")
  @patch("robosystems.dagster.sensors.sec.env")
  def test_discovers_unprocessed_filings(
    self, mock_env, mock_get_client, mock_get_years
  ):
    """Test sensor discovers unprocessed filings and yields run requests."""
    mock_env.ENVIRONMENT = "prod"
    mock_env.SHARED_RAW_BUCKET = "test-raw"

    mock_get_years.return_value = ["2024", "2025"]

    # Mock S3 listing for raw files
    mock_client = MagicMock()
    mock_paginator = MagicMock()
    mock_paginator.paginate.return_value = [
      {
        "Contents": [
          {"Key": "sec/year=2024/320193/0000320193-24-000081.zip"},
          {"Key": "sec/year=2024/320193/0000320193-24-000082.zip"},
        ]
      }
    ]
    mock_client.get_paginator.return_value = mock_paginator
    mock_get_client.return_value = mock_client

    # Build context with mock instance
    context = build_sensor_context()
    context._instance = MagicMock()
    # Second filing is already materialized
    context._instance.get_materialized_partitions.return_value = {
      "2024_320193_0000320193-24-000082"
    }

    result = list(sec_processing_sensor(context))

    # Should yield 1 run request (only unprocessed filing)
    assert len(result) == 1
    assert result[0].partition_key == "2024_320193_0000320193-24-000081"

  @patch("robosystems.dagster.sensors.sec._get_years_to_scan")
  @patch("robosystems.dagster.sensors.sec._get_s3_client")
  @patch("robosystems.dagster.sensors.sec.env")
  def test_handles_empty_bucket(self, mock_env, mock_get_client, mock_get_years):
    """Test sensor handles empty bucket gracefully."""
    mock_env.ENVIRONMENT = "prod"
    mock_env.SHARED_RAW_BUCKET = "test-raw"

    mock_get_years.return_value = ["2024"]

    mock_client = MagicMock()
    mock_paginator = MagicMock()
    mock_paginator.paginate.return_value = [{"Contents": []}]
    mock_client.get_paginator.return_value = mock_paginator
    mock_get_client.return_value = mock_client

    context = build_sensor_context()
    result = list(sec_processing_sensor(context))

    # Should return SkipReason (completed scan)
    assert len(result) == 1
    assert "Completed" in str(result[0])

  @patch("robosystems.dagster.sensors.sec._get_years_to_scan")
  @patch("robosystems.dagster.sensors.sec._get_s3_client")
  @patch("robosystems.dagster.sensors.sec.env")
  def test_all_filings_materialized(self, mock_env, mock_get_client, mock_get_years):
    """Test sensor handles case where all filings are already materialized."""
    mock_env.ENVIRONMENT = "prod"
    mock_env.SHARED_RAW_BUCKET = "test-raw"

    mock_get_years.return_value = ["2024"]

    mock_client = MagicMock()
    mock_paginator = MagicMock()
    mock_paginator.paginate.return_value = [
      {"Contents": [{"Key": "sec/year=2024/320193/0000320193-24-000081.zip"}]}
    ]
    mock_client.get_paginator.return_value = mock_paginator
    mock_get_client.return_value = mock_client

    context = build_sensor_context()
    context._instance = MagicMock()
    # All filings are materialized
    context._instance.get_materialized_partitions.return_value = {
      "2024_320193_0000320193-24-000081"
    }

    result = list(sec_processing_sensor(context))

    # Should return SkipReason (all filings already materialized)
    assert len(result) == 1
    assert "materialized" in str(result[0])

  @patch("robosystems.dagster.sensors.sec._get_years_to_scan")
  @patch("robosystems.dagster.sensors.sec._get_s3_client")
  @patch("robosystems.dagster.sensors.sec.env")
  def test_cursor_progression(self, mock_env, mock_get_client, mock_get_years):
    """Test that cursor progresses through years (newest to oldest)."""
    mock_env.ENVIRONMENT = "prod"
    mock_env.SHARED_RAW_BUCKET = "test-raw"

    mock_get_years.return_value = ["2025", "2024"]  # Newest first

    mock_client = MagicMock()
    mock_paginator = MagicMock()
    # 2025 is empty, should move to 2024
    mock_paginator.paginate.return_value = [{"Contents": []}]
    mock_client.get_paginator.return_value = mock_paginator
    mock_get_client.return_value = mock_client

    # Start with cursor pointing to 2025 (newest)
    cursor = json.dumps({"year": "2025", "last_key": None})
    context = build_sensor_context(cursor=cursor)
    result = list(sec_processing_sensor(context))

    # Should skip to next year (2024)
    assert len(result) == 1
    assert "2024" in str(result[0])

  @patch("robosystems.dagster.sensors.sec._get_years_to_scan")
  @patch("robosystems.dagster.sensors.sec._get_s3_client")
  @patch("robosystems.dagster.sensors.sec.env")
  def test_cursor_resets_after_all_years_exhausted(
    self, mock_env, mock_get_client, mock_get_years
  ):
    """Test that cursor resets when all years have been scanned."""
    mock_env.ENVIRONMENT = "prod"
    mock_env.SHARED_RAW_BUCKET = "test-raw"

    # Only one year to scan
    mock_get_years.return_value = ["2024"]

    mock_client = MagicMock()
    mock_paginator = MagicMock()
    # No files in the year - should trigger reset
    mock_paginator.paginate.return_value = [{"Contents": []}]
    mock_client.get_paginator.return_value = mock_paginator
    mock_get_client.return_value = mock_client

    # Cursor pointing to the last (and only) year
    cursor = json.dumps({"year": "2024", "last_key": None})
    context = build_sensor_context(cursor=cursor)

    result = list(sec_processing_sensor(context))

    # Should return SkipReason about completing full scan
    assert len(result) == 1
    assert "Completed full scan" in str(result[0]) or "restarting" in str(result[0]).lower()
