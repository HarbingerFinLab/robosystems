"""Tests for SEC staging models.

Tests the staging manifest and result models used for decoupled
DuckDB staging and LadybugDB materialization.
"""

import json
import tempfile
from pathlib import Path

import pytest

from robosystems.adapters.sec.models.staging import (
  MaterializeResult,
  StagingManifest,
  StagingResult,
  TableInfo,
)


class TestTableInfo:
  """Tests for TableInfo dataclass."""

  def test_to_dict(self):
    """Test TableInfo serialization to dict."""
    info = TableInfo(
      name="Entity",
      row_count=1000,
      file_count=50,
      staged_at="2025-01-15T10:00:00Z",
    )
    result = info.to_dict()

    assert result["name"] == "Entity"
    assert result["row_count"] == 1000
    assert result["file_count"] == 50
    assert result["staged_at"] == "2025-01-15T10:00:00Z"

  def test_from_dict(self):
    """Test TableInfo deserialization from dict."""
    data = {
      "name": "Fact",
      "row_count": 50000,
      "file_count": 200,
      "staged_at": "2025-01-15T11:00:00Z",
    }
    info = TableInfo.from_dict(data)

    assert info.name == "Fact"
    assert info.row_count == 50000
    assert info.file_count == 200
    assert info.staged_at == "2025-01-15T11:00:00Z"

  def test_roundtrip(self):
    """Test TableInfo serialization roundtrip."""
    original = TableInfo(
      name="Element",
      row_count=5000,
      file_count=100,
      staged_at="2025-01-15T12:00:00Z",
    )
    restored = TableInfo.from_dict(original.to_dict())

    assert restored.name == original.name
    assert restored.row_count == original.row_count
    assert restored.file_count == original.file_count
    assert restored.staged_at == original.staged_at


class TestStagingResult:
  """Tests for StagingResult dataclass."""

  def test_success_result(self):
    """Test successful staging result."""
    result = StagingResult(
      status="success",
      table_names=["Entity", "Fact", "Element"],
      total_files=500,
      total_rows=100000,
      duration_seconds=120.5,
      manifest_path="/data/staging/sec_manifest.json",
      duckdb_path="/data/staging/sec.duckdb",
    )

    assert result.status == "success"
    assert len(result.table_names) == 3
    assert result.total_files == 500
    assert result.total_rows == 100000
    assert result.error is None

  def test_error_result(self):
    """Test error staging result."""
    result = StagingResult(
      status="error",
      table_names=[],
      error="Graph client initialization failed",
      duration_seconds=0.5,
    )

    assert result.status == "error"
    assert result.error == "Graph client initialization failed"
    assert len(result.table_names) == 0

  def test_to_dict(self):
    """Test StagingResult serialization."""
    result = StagingResult(
      status="success",
      table_names=["Entity"],
      total_files=10,
      total_rows=1000,
      duration_seconds=5.0,
    )
    data = result.to_dict()

    assert data["status"] == "success"
    assert data["table_names"] == ["Entity"]
    assert data["total_files"] == 10
    assert data["total_rows"] == 1000


class TestMaterializeResult:
  """Tests for MaterializeResult dataclass."""

  def test_success_result(self):
    """Test successful materialization result."""
    result = MaterializeResult(
      status="success",
      total_rows_ingested=50000,
      total_time_ms=30000.0,
      tables=[
        {"table_name": "Entity", "rows_ingested": 1000, "status": "success"},
        {"table_name": "Fact", "rows_ingested": 49000, "status": "success"},
      ],
    )

    assert result.status == "success"
    assert result.total_rows_ingested == 50000
    assert len(result.tables) == 2

  def test_error_result(self):
    """Test error materialization result."""
    result = MaterializeResult(
      status="error",
      error="Staging manifest not found",
    )

    assert result.status == "error"
    assert result.error == "Staging manifest not found"

  def test_to_dict(self):
    """Test MaterializeResult serialization."""
    result = MaterializeResult(
      status="success",
      total_rows_ingested=1000,
      total_time_ms=5000.0,
    )
    data = result.to_dict()

    assert data["status"] == "success"
    assert data["total_rows_ingested"] == 1000
    assert data["total_time_ms"] == 5000.0


class TestStagingManifest:
  """Tests for StagingManifest dataclass."""

  def test_default_values(self):
    """Test manifest with default values."""
    manifest = StagingManifest()

    assert manifest.version == "1.0"
    assert manifest.mode == "full"
    assert manifest.status == "complete"
    assert manifest.graph_id == "sec"
    assert manifest.staged_at != ""  # Should auto-populate

  def test_custom_values(self):
    """Test manifest with custom values."""
    manifest = StagingManifest(
      version="1.0",
      staged_at="2025-01-15T10:00:00Z",
      mode="incremental",
      source={"year": 2025, "bucket": "test-bucket"},
      tables={
        "Entity": TableInfo(
          name="Entity",
          row_count=1000,
          file_count=50,
          staged_at="2025-01-15T10:00:00Z",
        ),
      },
      status="complete",
      graph_id="sec",
    )

    assert manifest.mode == "incremental"
    assert manifest.source["year"] == 2025
    assert "Entity" in manifest.tables
    assert manifest.tables["Entity"].row_count == 1000

  def test_to_dict(self):
    """Test manifest serialization to dict."""
    manifest = StagingManifest(
      staged_at="2025-01-15T10:00:00Z",
      source={"year": 2025},
      tables={
        "Entity": TableInfo(
          name="Entity",
          row_count=1000,
          file_count=50,
          staged_at="2025-01-15T10:00:00Z",
        ),
      },
    )
    data = manifest.to_dict()

    assert data["version"] == "1.0"
    assert data["staged_at"] == "2025-01-15T10:00:00Z"
    assert data["tables"]["Entity"]["row_count"] == 1000

  def test_from_dict(self):
    """Test manifest deserialization from dict."""
    data = {
      "version": "1.0",
      "staged_at": "2025-01-15T10:00:00Z",
      "mode": "full",
      "source": {"year": 2025},
      "tables": {
        "Entity": {
          "name": "Entity",
          "row_count": 1000,
          "file_count": 50,
          "staged_at": "2025-01-15T10:00:00Z",
        },
      },
      "status": "complete",
      "graph_id": "sec",
    }
    manifest = StagingManifest.from_dict(data)

    assert manifest.version == "1.0"
    assert manifest.tables["Entity"].row_count == 1000

  def test_save_and_load(self):
    """Test manifest save and load to/from file."""
    manifest = StagingManifest(
      staged_at="2025-01-15T10:00:00Z",
      source={"year": 2025, "bucket": "test-bucket"},
      tables={
        "Entity": TableInfo(
          name="Entity",
          row_count=1000,
          file_count=50,
          staged_at="2025-01-15T10:00:00Z",
        ),
        "Fact": TableInfo(
          name="Fact",
          row_count=50000,
          file_count=200,
          staged_at="2025-01-15T10:05:00Z",
        ),
      },
    )

    with tempfile.TemporaryDirectory() as tmpdir:
      path = Path(tmpdir) / "manifest.json"
      manifest.save(str(path))

      # Verify file exists
      assert path.exists()

      # Verify JSON is valid
      with open(path) as f:
        data = json.load(f)
      assert data["version"] == "1.0"

      # Load and verify
      loaded = StagingManifest.load(str(path))
      assert loaded.version == manifest.version
      assert loaded.staged_at == manifest.staged_at
      assert len(loaded.tables) == 2
      assert loaded.tables["Entity"].row_count == 1000
      assert loaded.tables["Fact"].row_count == 50000

  def test_exists(self):
    """Test manifest exists check."""
    with tempfile.TemporaryDirectory() as tmpdir:
      path = Path(tmpdir) / "manifest.json"

      # Should not exist
      assert not StagingManifest.exists(str(path))

      # Create and check again
      manifest = StagingManifest()
      manifest.save(str(path))
      assert StagingManifest.exists(str(path))

  def test_get_table_names(self):
    """Test getting list of table names."""
    manifest = StagingManifest(
      tables={
        "Entity": TableInfo(name="Entity", row_count=100, file_count=10, staged_at=""),
        "Fact": TableInfo(name="Fact", row_count=200, file_count=20, staged_at=""),
      },
    )

    names = manifest.get_table_names()
    assert "Entity" in names
    assert "Fact" in names
    assert len(names) == 2

  def test_is_complete(self):
    """Test checking manifest completion status."""
    complete_manifest = StagingManifest(status="complete")
    partial_manifest = StagingManifest(status="partial")

    assert complete_manifest.is_complete()
    assert not partial_manifest.is_complete()

  def test_get_total_rows(self):
    """Test getting total row count."""
    manifest = StagingManifest(
      tables={
        "Entity": TableInfo(name="Entity", row_count=1000, file_count=10, staged_at=""),
        "Fact": TableInfo(name="Fact", row_count=50000, file_count=200, staged_at=""),
      },
    )

    assert manifest.get_total_rows() == 51000

  def test_get_total_files(self):
    """Test getting total file count."""
    manifest = StagingManifest(
      tables={
        "Entity": TableInfo(name="Entity", row_count=1000, file_count=10, staged_at=""),
        "Fact": TableInfo(name="Fact", row_count=50000, file_count=200, staged_at=""),
      },
    )

    assert manifest.get_total_files() == 210

  def test_load_nonexistent_file(self):
    """Test loading from nonexistent file raises error."""
    with pytest.raises(FileNotFoundError):
      StagingManifest.load("/nonexistent/path/manifest.json")
