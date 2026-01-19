"""Tests for graph API table management endpoints."""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from robosystems.graph_api.core.duckdb.manager import TableInfo
from robosystems.graph_api.routers.databases.tables import management


@pytest.fixture
def client(monkeypatch):
  app = FastAPI()
  app.include_router(management.router)

  fake_manager = SimpleNamespace(
    create_table=None,
    list_tables=None,
    delete_table=None,
  )
  monkeypatch.setattr(management, "table_manager", fake_manager)

  # Mock the staging_task_manager for async operations
  fake_staging_manager = AsyncMock()
  fake_staging_manager.create_task.return_value = "staging_test_Entity_abc123"
  monkeypatch.setattr(management, "staging_task_manager", fake_staging_manager)

  test_client = TestClient(app)
  test_client.fake_manager = fake_manager
  test_client.fake_staging_manager = fake_staging_manager
  return test_client


def test_create_table_success(client):
  """Test that create_table returns an async task response."""
  response = client.post(
    "/databases/graph-123/tables",
    json={
      "graph_id": "graph-ignored",
      "table_name": "Entity",
      "s3_pattern": "s3://bucket/user/foo/graph-123/Entity/*.parquet",
    },
  )

  assert response.status_code == 200
  payload = response.json()
  # New SSE-based response format
  assert payload["task_id"] == "staging_test_Entity_abc123"
  assert payload["status"] == "started"
  assert payload["sse_url"] == "/tasks/staging_test_Entity_abc123/monitor"
  assert "Background table creation started" in payload["message"]


def test_create_table_with_multiple_files(client):
  """Test create_table with a list of S3 files."""
  response = client.post(
    "/databases/graph-123/tables",
    json={
      "graph_id": "graph-ignored",
      "table_name": "Entity",
      "s3_pattern": [
        "s3://bucket/file1.parquet",
        "s3://bucket/file2.parquet",
        "s3://bucket/file3.parquet",
      ],
    },
  )

  assert response.status_code == 200
  payload = response.json()
  assert payload["status"] == "started"
  assert "task_id" in payload


def test_list_tables(client):
  client.fake_manager.list_tables = lambda graph_id: [
    TableInfo(
      graph_id=graph_id,
      table_name="Entity",
      size_bytes=1024,
      row_count=123,
      s3_location="s3://bucket/user/graph-123/Entity/**",
    )
  ]

  response = client.get("/databases/graph-123/tables")

  assert response.status_code == 200
  payload = response.json()
  assert payload[0]["table_name"] == "Entity"


def test_delete_table(client):
  client.fake_manager.delete_table = lambda graph_id, table_name: {
    "status": "deleted",
    "table_name": table_name,
  }

  response = client.delete("/databases/graph-123/tables/Entity")

  assert response.status_code == 200
  payload = response.json()
  assert payload["status"] == "deleted"
