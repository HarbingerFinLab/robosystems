"""Staging manifest and result models for SEC DuckDB-based ingestion.

These models support decoupled staging and materialization:
- StagingResult: Returned by stage_to_duckdb() with staging statistics
- MaterializeResult: Returned by materialize_from_duckdb() with ingestion statistics
- StagingManifest: Persisted to disk to track staging state for recovery
"""

import json
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any


@dataclass
class TableInfo:
  """Information about a staged table."""

  name: str
  row_count: int
  file_count: int
  staged_at: str  # ISO timestamp

  def to_dict(self) -> dict[str, Any]:
    """Convert to dictionary for JSON serialization."""
    return {
      "name": self.name,
      "row_count": self.row_count,
      "file_count": self.file_count,
      "staged_at": self.staged_at,
    }

  @classmethod
  def from_dict(cls, data: dict[str, Any]) -> "TableInfo":
    """Create from dictionary."""
    return cls(
      name=data["name"],
      row_count=data["row_count"],
      file_count=data["file_count"],
      staged_at=data["staged_at"],
    )


@dataclass
class StagingResult:
  """Result from stage_to_duckdb() operation.

  Contains statistics about the staging operation and the list of
  tables that were successfully staged.
  """

  status: str  # "success", "partial", "error"
  table_names: list[str]  # Successfully staged tables
  tables: dict[str, TableInfo] = field(default_factory=dict)
  total_files: int = 0
  total_rows: int = 0
  duration_seconds: float = 0.0
  manifest_path: str | None = None
  duckdb_path: str | None = None
  error: str | None = None

  def to_dict(self) -> dict[str, Any]:
    """Convert to dictionary for metadata output."""
    return {
      "status": self.status,
      "table_names": self.table_names,
      "tables": {name: info.to_dict() for name, info in self.tables.items()},
      "total_files": self.total_files,
      "total_rows": self.total_rows,
      "duration_seconds": self.duration_seconds,
      "manifest_path": self.manifest_path,
      "duckdb_path": self.duckdb_path,
      "error": self.error,
    }


@dataclass
class MaterializeResult:
  """Result from materialize_from_duckdb() operation.

  Contains statistics about the materialization (ingestion) operation.
  """

  status: str  # "success", "partial", "error"
  total_rows_ingested: int = 0
  total_time_ms: float = 0.0
  tables: list[dict[str, Any]] = field(default_factory=list)
  error: str | None = None

  def to_dict(self) -> dict[str, Any]:
    """Convert to dictionary for metadata output."""
    return {
      "status": self.status,
      "total_rows_ingested": self.total_rows_ingested,
      "total_time_ms": self.total_time_ms,
      "tables": self.tables,
      "error": self.error,
    }


@dataclass
class StagingManifest:
  """Persistent manifest tracking DuckDB staging state.

  This manifest is written to disk after successful staging and read
  before materialization to verify the staging is complete and valid.

  Attributes:
      version: Manifest format version
      staged_at: ISO timestamp when staging completed
      mode: "full" or "incremental"
      source: Information about source data (partitions, years, etc.)
      tables: Dictionary of table name to TableInfo
      status: "complete" or "partial"
      graph_id: The graph database identifier
  """

  version: str = "1.0"
  staged_at: str = ""
  mode: str = "full"  # "full" or "incremental"
  source: dict[str, Any] = field(default_factory=dict)
  tables: dict[str, TableInfo] = field(default_factory=dict)
  status: str = "complete"  # "complete" or "partial"
  graph_id: str = "sec"

  def __post_init__(self):
    """Set default staged_at if not provided."""
    if not self.staged_at:
      self.staged_at = datetime.now(UTC).isoformat()

  def to_dict(self) -> dict[str, Any]:
    """Convert to dictionary for JSON serialization."""
    return {
      "version": self.version,
      "staged_at": self.staged_at,
      "mode": self.mode,
      "source": self.source,
      "tables": {name: info.to_dict() for name, info in self.tables.items()},
      "status": self.status,
      "graph_id": self.graph_id,
    }

  @classmethod
  def from_dict(cls, data: dict[str, Any]) -> "StagingManifest":
    """Create from dictionary."""
    tables = {}
    for name, info in data.get("tables", {}).items():
      tables[name] = TableInfo.from_dict(info)

    return cls(
      version=data.get("version", "1.0"),
      staged_at=data.get("staged_at", ""),
      mode=data.get("mode", "full"),
      source=data.get("source", {}),
      tables=tables,
      status=data.get("status", "complete"),
      graph_id=data.get("graph_id", "sec"),
    )

  def save(self, path: str) -> None:
    """Save manifest to file.

    Args:
        path: File path to save to
    """
    import os

    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as f:
      json.dump(self.to_dict(), f, indent=2)

  @classmethod
  def load(cls, path: str) -> "StagingManifest":
    """Load manifest from file.

    Args:
        path: File path to load from

    Returns:
        StagingManifest instance

    Raises:
        FileNotFoundError: If the manifest file doesn't exist
        json.JSONDecodeError: If the file is not valid JSON
    """
    with open(path) as f:
      data = json.load(f)
    return cls.from_dict(data)

  @classmethod
  def exists(cls, path: str) -> bool:
    """Check if manifest file exists.

    Args:
        path: File path to check

    Returns:
        True if the manifest file exists
    """
    import os

    return os.path.exists(path)

  def get_table_names(self) -> list[str]:
    """Get list of staged table names."""
    return list(self.tables.keys())

  def is_complete(self) -> bool:
    """Check if staging is complete."""
    return self.status == "complete"

  def get_total_rows(self) -> int:
    """Get total row count across all tables."""
    return sum(info.row_count for info in self.tables.values())

  def get_total_files(self) -> int:
    """Get total file count across all tables."""
    return sum(info.file_count for info in self.tables.values())
