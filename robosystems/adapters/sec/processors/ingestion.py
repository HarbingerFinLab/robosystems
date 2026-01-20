"""
XBRL Graph Ingestion Processor (DuckDB-Based).

This module provides a decoupled ingestion approach using DuckDB staging tables
with persistent storage for independent retry of staging and materialization.

Architecture:
- **Stage 1 (stage_to_duckdb)**: Discover S3 files → Create DuckDB tables → Write manifest
- **Stage 2 (materialize_from_duckdb)**: Read manifest → Materialize to LadybugDB

Key benefits of decoupled stages:
- If LadybugDB materialization fails, don't lose 2+ hours of DuckDB staging work
- Staging persists to disk, enabling independent retry of materialization
- Manifest tracks staging state for recovery and monitoring

Flow:
1. stage_to_duckdb(): Discover processed Parquet files → Create DuckDB tables → Persist
2. materialize_from_duckdb(): Read from persisted DuckDB → Ingest to LadybugDB

Backward Compatibility:
- process_files() still works as before (runs staging then materialization)
- New Dagster assets can call staging and materialization independently

LIMITATION: This approach currently ALWAYS rebuilds the graph from scratch because it
discovers and loads ALL files from S3, not just new/changed files. This is fundamentally
different from the COPY-based approach which works incrementally with consolidated files.

Status: Production - enables independent retry of failed materialization.
"""

import time
from datetime import UTC, datetime
from typing import Any

from robosystems.adapters.sec.models.staging import (
  MaterializeResult,
  StagingManifest,
  StagingResult,
  TableInfo,
)
from robosystems.config import env
from robosystems.config.storage.shared import (
  DATA_SOURCES,
  DataSourceType,
  get_staging_duckdb_path,
  get_staging_manifest_path,
)
from robosystems.graph_api.client.factory import get_graph_client
from robosystems.logger import logger
from robosystems.operations.aws.s3 import S3Client


class XBRLDuckDBGraphProcessor:
  """
  XBRL graph data processor using DuckDB-based ingestion pattern.

  This processor communicates with the Graph API to:
  1. Create DuckDB staging tables from processed Parquet files
  2. Trigger ingestion to graph database

  Architecture:
  - Uses Graph API client to communicate with Graph API container
  - DuckDB pool lives on Graph API side, not on worker
  - Works directly with processed files (many small files) instead of
    consolidated files to test performance with high file counts
  """

  def __init__(self, graph_id: str = "sec", source_prefix: str | None = None):
    """
    Initialize XBRL graph ingestion processor.

    Args:
        graph_id: Graph database identifier (default: "sec")
        source_prefix: S3 prefix for source files (default: SEC prefix from shared_data.py)
    """
    self.graph_id = graph_id
    self.s3_client = S3Client()
    self.bucket = env.SHARED_PROCESSED_BUCKET
    # Use SEC prefix from centralized config if not specified
    sec_config = DATA_SOURCES[DataSourceType.SEC]
    self.source_prefix = source_prefix or sec_config.processed_prefix.rstrip("/")

  async def stage_to_duckdb(
    self,
    rebuild: bool = True,
    year: int | None = None,
    reset_staging: bool = False,
  ) -> StagingResult:
    """
    Stage processed Parquet files to persistent DuckDB.

    This is Stage 1 of the decoupled pipeline. It discovers processed files,
    optionally rebuilds the LadybugDB database, creates DuckDB staging tables,
    and writes a manifest for recovery.

    The staging result and manifest are persisted, enabling materialize_from_duckdb()
    to run independently (e.g., after a failed materialization attempt).

    Args:
        rebuild: Whether to rebuild LadybugDB database from scratch (default: True).
        year: Optional year filter. If provided, only files from that year
              will be included in staging.
        reset_staging: If True, delete DuckDB staging alongside LadybugDB for a
            fresh start. If False (default), preserve DuckDB for incremental scenarios.

    Returns:
        StagingResult with table counts, file counts, and manifest path
    """
    start_time = time.time()

    logger.info(
      f"Starting DuckDB staging for graph {self.graph_id} "
      f"(year={year or 'all'}, rebuild={rebuild}, reset_staging={reset_staging})"
    )

    manifest_path = get_staging_manifest_path(self.graph_id)
    duckdb_path = get_staging_duckdb_path(self.graph_id)

    try:
      # Get graph client for API calls
      try:
        client = await get_graph_client(graph_id=self.graph_id, operation_type="write")
      except Exception as client_err:
        logger.error(
          f"Failed to initialize graph client for {self.graph_id}: {client_err}",
          exc_info=True,
        )
        return StagingResult(
          status="error",
          table_names=[],
          error=f"Graph client initialization failed: {client_err!s}",
          duration_seconds=time.time() - start_time,
        )

      # Step 1: Discover processed files
      logger.info("Step 1: Discovering processed Parquet files...")
      tables_info = await self._discover_processed_files(year)

      if not tables_info:
        logger.warning("No processed files found")
        return StagingResult(
          status="no_data",
          table_names=[],
          error="No processed files found",
          duration_seconds=time.time() - start_time,
        )

      logger.info(f"Found {len(tables_info)} tables to stage")
      total_files = sum(len(files) for files in tables_info.values())
      logger.info(f"Total files: {total_files}")

      # Step 2: Handle LadybugDB database rebuild BEFORE creating DuckDB tables
      if rebuild:
        logger.info("Step 2: Rebuilding LadybugDB database...")
        await self._rebuild_ladybug_database(client, reset_staging=reset_staging)

      # Step 3: Create DuckDB staging tables via Graph API
      logger.info("Step 3: Creating DuckDB staging tables via Graph API...")
      successful_tables, table_infos = await self._create_duckdb_tables_with_info(
        tables_info, client
      )

      # Step 4: Write staging manifest
      logger.info("Step 4: Writing staging manifest...")
      manifest = StagingManifest(
        version="1.0",
        staged_at=datetime.now(UTC).isoformat(),
        mode="full",
        source={
          "year": year,
          "bucket": self.bucket,
          "prefix": self.source_prefix,
        },
        tables=table_infos,
        status="complete" if len(successful_tables) == len(tables_info) else "partial",
        graph_id=self.graph_id,
      )
      manifest.save(manifest_path)
      logger.info(f"Manifest saved to {manifest_path}")

      duration = time.time() - start_time

      logger.info(
        f"DuckDB staging complete in {duration:.2f}s: "
        f"{len(successful_tables)} tables from {total_files} files"
      )

      return StagingResult(
        status="success",
        table_names=successful_tables,
        tables=table_infos,
        total_files=total_files,
        total_rows=sum(info.row_count for info in table_infos.values()),
        duration_seconds=duration,
        manifest_path=manifest_path,
        duckdb_path=duckdb_path,
      )

    except Exception as e:
      logger.error(f"DuckDB staging failed: {e}", exc_info=True)
      return StagingResult(
        status="error",
        table_names=[],
        error=str(e),
        duration_seconds=time.time() - start_time,
      )

  async def materialize_from_duckdb(
    self,
    table_names: list[str] | None = None,
  ) -> MaterializeResult:
    """
    Materialize LadybugDB graph from existing DuckDB staging.

    This is Stage 2 of the decoupled pipeline. It reads the staging manifest,
    verifies staging is complete, and triggers ingestion for each table.

    Precondition: stage_to_duckdb() must have been run successfully, creating
    a valid staging_manifest.json file.

    Args:
        table_names: Optional list of specific tables to materialize.
                     If None, materializes all tables from the manifest.

    Returns:
        MaterializeResult with rows ingested per table
    """
    start_time = time.time()

    manifest_path = get_staging_manifest_path(self.graph_id)

    logger.info(f"Starting materialization from DuckDB for graph {self.graph_id}")

    try:
      # Step 1: Read and verify staging manifest
      logger.info("Step 1: Reading staging manifest...")
      if not StagingManifest.exists(manifest_path):
        error_msg = (
          f"Staging manifest not found at {manifest_path}. Run stage_to_duckdb() first."
        )
        logger.error(error_msg)
        return MaterializeResult(
          status="error",
          error=error_msg,
        )

      manifest = StagingManifest.load(manifest_path)
      logger.info(
        f"Manifest loaded: {len(manifest.tables)} tables, "
        f"staged at {manifest.staged_at}, status={manifest.status}"
      )

      if manifest.status != "complete":
        logger.warning(
          f"Staging manifest status is '{manifest.status}', not 'complete'. "
          "Some tables may be missing."
        )

      # Step 2: Determine which tables to materialize
      if table_names is None:
        table_names = manifest.get_table_names()

      if not table_names:
        logger.warning("No tables to materialize")
        return MaterializeResult(
          status="no_data",
          error="No tables found in manifest",
        )

      logger.info(f"Materializing {len(table_names)} tables...")

      # Step 3: Get graph client
      try:
        client = await get_graph_client(graph_id=self.graph_id, operation_type="write")
      except Exception as client_err:
        logger.error(
          f"Failed to initialize graph client for {self.graph_id}: {client_err}",
          exc_info=True,
        )
        return MaterializeResult(
          status="error",
          error=f"Graph client initialization failed: {client_err!s}",
        )

      # Step 4: Ensure LadybugDB database exists with schema
      # This handles retry scenarios where LadybugDB was deleted but DuckDB preserved
      # Uses ensure_shared_repository_exists for production-compatible routing
      logger.info("Step 4: Ensuring LadybugDB database exists with schema...")
      from robosystems.config import env
      from robosystems.operations.graph.shared_repository_service import (
        ensure_shared_repository_exists,
      )

      repo_result = await ensure_shared_repository_exists(
        repository_name=self.graph_id,
        created_by="system",
        instance_id="local-dev" if env.ENVIRONMENT == "dev" else "ladybug-shared-prod",
      )
      logger.info(f"Repository ensure result: {repo_result.get('status', 'unknown')}")

      # Step 5: Trigger ingestion for each table
      logger.info("Step 5: Triggering graph ingestion...")
      ingestion_results = await self._trigger_ingestion(
        table_names, client, rebuild=False
      )

      duration = time.time() - start_time

      logger.info(
        f"Materialization complete in {duration:.2f}s: "
        f"{ingestion_results.get('total_rows_ingested', 0)} rows ingested"
      )

      return MaterializeResult(
        status="success",
        total_rows_ingested=ingestion_results.get("total_rows_ingested", 0),
        total_time_ms=ingestion_results.get("total_time_ms", 0),
        tables=ingestion_results.get("tables", []),
      )

    except Exception as e:
      logger.error(f"Materialization failed: {e}", exc_info=True)
      return MaterializeResult(
        status="error",
        error=str(e),
      )

  async def process_files(
    self,
    rebuild: bool = True,
    year: int | None = None,
  ) -> dict[str, Any]:
    """
    Process Parquet files into graph database using DuckDB-based pattern.

    This is the backward-compatible method that runs both staging and
    materialization in sequence. For independent control, use:
    - stage_to_duckdb() for staging only
    - materialize_from_duckdb() for materialization only

    IMPORTANT: This approach always rebuilds the graph from scratch because
    DuckDB staging tables contain ALL processed files from S3.

    Args:
        rebuild: Whether to rebuild graph from scratch (default: True).
        year: Optional year filter for processing.

    Returns:
        Processing results with statistics
    """
    start_time = time.time()

    logger.info(
      f"Starting DuckDB-based SEC ingestion for graph {self.graph_id} "
      f"(year={year or 'all'}, rebuild={rebuild})"
    )

    # Stage 1: DuckDB staging
    staging_result = await self.stage_to_duckdb(rebuild=rebuild, year=year)

    if staging_result.status == "error":
      return {
        "status": "error",
        "error": staging_result.error,
        "duration_seconds": time.time() - start_time,
      }

    if staging_result.status == "no_data":
      return {
        "status": "no_data",
        "message": "No processed files found",
        "duration_seconds": time.time() - start_time,
      }

    # Stage 2: LadybugDB materialization
    materialize_result = await self.materialize_from_duckdb(
      table_names=staging_result.table_names
    )

    duration = time.time() - start_time

    if materialize_result.status == "error":
      return {
        "status": "error",
        "error": materialize_result.error,
        "staging_complete": True,  # Staging succeeded, materialization failed
        "duration_seconds": duration,
      }

    logger.info(
      f"SEC DuckDB-based ingestion complete in {duration:.2f}s: "
      f"{materialize_result.total_rows_ingested} rows ingested from {staging_result.total_files} files"
    )

    return {
      "status": "success",
      "tables_processed": len(staging_result.table_names),
      "total_files": staging_result.total_files,
      "ingestion_results": materialize_result.to_dict(),
      "duration_seconds": duration,
    }

  async def _rebuild_ladybug_database(
    self, client, reset_staging: bool = False
  ) -> None:
    """Rebuild the LadybugDB database from scratch.

    Deletes the existing database and recreates it with the same schema.

    Args:
        client: Graph API client instance
        reset_staging: If True, also delete DuckDB staging for a fresh start.
            If False (default), preserve DuckDB for incremental/retry scenarios.
    """
    from robosystems.database import SessionFactory
    from robosystems.models.iam import GraphSchema

    logger.info(
      f"Rebuild requested - regenerating entire LadybugDB database for {self.graph_id}"
    )

    db = SessionFactory()
    try:
      # preserve_duckdb=True keeps DuckDB for retry/incremental scenarios
      # preserve_duckdb=False (reset_staging=True) deletes both for fresh start
      preserve_duckdb = not reset_staging
      await client.delete_database(self.graph_id, preserve_duckdb=preserve_duckdb)
      if reset_staging:
        logger.info(
          f"Deleted LadybugDB and DuckDB staging: {self.graph_id} (fresh start)"
        )
      else:
        logger.info(
          f"Deleted LadybugDB database: {self.graph_id} (DuckDB staging preserved)"
        )

      schema = GraphSchema.get_active_schema(self.graph_id, db)
      if not schema:
        raise ValueError(f"No schema found for graph {self.graph_id}")

      create_db_kwargs = {
        "graph_id": self.graph_id,
        "schema_type": schema.schema_type,
        "custom_schema_ddl": schema.schema_ddl,
      }

      if schema.schema_type == "shared":
        create_db_kwargs["repository_name"] = self.graph_id

      await client.create_database(**create_db_kwargs)
      logger.info(
        f"Recreated LadybugDB database with schema type: {schema.schema_type}"
      )
    finally:
      db.close()

  async def _discover_processed_files(
    self, year: int | None = None
  ) -> dict[str, list[str]]:
    """
    Discover processed Parquet files from S3.

    Scans the processed files directory structure:
    processed/year=YYYY/nodes/TableName/file.parquet
    processed/year=YYYY/relationships/TableName/file.parquet

    Args:
        year: Optional year filter. If None, scans all year subdirectories.

    Returns:
        Dictionary mapping table names to list of S3 keys
    """
    tables_info: dict[str, list[str]] = {}

    # Determine which years to scan
    if year is None:
      # Discover all year subdirectories by listing the processed/ prefix
      year_prefix = f"{self.source_prefix}/"
      logger.info(f"Discovering year subdirectories in {self.bucket}/{year_prefix}")

      # List directories to find year= subdirectories
      paginator = self.s3_client.s3_client.get_paginator("list_objects_v2")
      pages = paginator.paginate(Bucket=self.bucket, Prefix=year_prefix, Delimiter="/")

      years_to_scan = []
      for page in pages:
        # CommonPrefixes contains the "directories" (year= prefixes)
        if "CommonPrefixes" in page:
          for prefix_info in page["CommonPrefixes"]:
            prefix_path = prefix_info["Prefix"]
            # Extract year from prefix like "processed/year=2025/"
            if "year=" in prefix_path:
              year_part = prefix_path.split("year=")[1].rstrip("/")
              try:
                year_num = int(year_part)
                years_to_scan.append(year_num)
                logger.debug(f"Found year subdirectory: {year_num}")
              except ValueError:
                logger.debug(f"Skipping non-year prefix: {prefix_path}")

      if not years_to_scan:
        logger.warning(f"No year subdirectories found under {year_prefix}")
        return tables_info

      logger.info(
        f"Discovered {len(years_to_scan)} years to scan: {sorted(years_to_scan)}"
      )
    else:
      # Single year specified
      years_to_scan = [year]

    # Scan both nodes and relationships directories across all years
    for entity_type in ["nodes", "relationships"]:
      for scan_year in years_to_scan:
        prefix = f"{self.source_prefix}/year={scan_year}/{entity_type}/"
        logger.debug(f"Scanning S3 bucket {self.bucket} with prefix {prefix}")

        # List all files recursively
        paginator = self.s3_client.s3_client.get_paginator("list_objects_v2")
        pages = paginator.paginate(Bucket=self.bucket, Prefix=prefix)

        for page in pages:
          if "Contents" not in page:
            continue

          for obj in page["Contents"]:
            key = obj["Key"]

            # Skip non-Parquet files
            if not key.endswith(".parquet"):
              continue

            # Extract table name from path: processed/year=YYYY/nodes|relationships/TableName/file.parquet
            # Structure: processed/year=YYYY/nodes|relationships/TableName/CIK_ACCESSION.parquet
            path_parts = key.replace(prefix, "").split("/")

            # First part after nodes/ or relationships/ is the table name
            if len(path_parts) >= 2:
              table_name = path_parts[0]
            else:
              logger.debug(f"Skipping file with unexpected path structure: {key}")
              continue

            if table_name not in tables_info:
              tables_info[table_name] = []

            tables_info[table_name].append(key)

    logger.info(f"Discovered {len(tables_info)} tables with files:")
    for table_name, files in tables_info.items():
      logger.info(f"  - {table_name}: {len(files)} files")

    return tables_info

  async def _create_duckdb_tables_with_info(
    self,
    tables_info: dict[str, list[str]],
    graph_client,
  ) -> tuple[list[str], dict[str, TableInfo]]:
    """
    Create DuckDB staging tables and return detailed TableInfo for manifest.

    This is an enhanced version of _create_duckdb_tables() that also returns
    TableInfo objects with row counts and timestamps for the staging manifest.

    Args:
        tables_info: Dictionary mapping table names to S3 keys
        graph_client: Graph API client instance

    Returns:
        Tuple of (successful_table_names, table_info_dict)

    Raises:
        RuntimeError: If any tables failed to create (after attempting all)
    """
    successful_tables: list[str] = []
    table_infos: dict[str, TableInfo] = {}
    failed_tables: list[tuple[str, str]] = []

    for table_name, s3_keys in tables_info.items():
      logger.info(f"Creating DuckDB table: {table_name} ({len(s3_keys)} files)")

      # Build list of full S3 URIs
      s3_files = [f"s3://{self.bucket}/{key}" for key in s3_keys]

      try:
        # Use graph client to call Graph API's table creation endpoint
        # Client uses SSE monitoring for long-running table creation
        response = await graph_client.create_table(
          graph_id=self.graph_id,
          table_name=table_name,
          s3_pattern=s3_files,  # Actually a list of files, not a pattern
          timeout=1800,  # 30 minutes for large file sets
        )

        # Handle SSE-based response format
        if response.get("status") == "failed":
          error = response.get("error", "Unknown error")
          logger.error(f"Failed to create DuckDB table {table_name}: {error}")
          failed_tables.append((table_name, error))
          continue

        # Extract result from SSE response
        result = response.get("result", {})
        duration = response.get("duration_seconds", result.get("duration_seconds", 0))
        row_count = result.get("row_count", 0)

        logger.info(
          f"Created DuckDB table {table_name} in {duration:.1f}s "
          f"(from {len(s3_keys)} files, {row_count} rows)"
        )

        successful_tables.append(table_name)
        table_infos[table_name] = TableInfo(
          name=table_name,
          row_count=row_count,
          file_count=len(s3_keys),
          staged_at=datetime.now(UTC).isoformat(),
        )

      except Exception as e:
        logger.error(f"Failed to create DuckDB table {table_name}: {e}")
        failed_tables.append((table_name, str(e)))
        continue

    # Report summary
    if failed_tables:
      logger.warning(
        f"DuckDB table creation: {len(successful_tables)} succeeded, "
        f"{len(failed_tables)} failed"
      )
      for table_name, error in failed_tables:
        logger.error(f"  Failed: {table_name} - {error}")

      # Raise after attempting all tables so we can see partial results
      raise RuntimeError(
        f"Failed to create {len(failed_tables)} DuckDB tables: "
        f"{[t[0] for t in failed_tables]}"
      )

    return successful_tables, table_infos

  async def _create_duckdb_tables(
    self,
    tables_info: dict[str, list[str]],
    graph_client,
  ) -> list[str]:
    """
    Create DuckDB staging tables for each discovered table via Graph API.

    Uses SSE monitoring to handle long-running table creation from thousands
    of S3 files without HTTP timeout issues. Tables are created sequentially
    to avoid overwhelming the instance.

    Continues processing remaining tables on failure to maximize debugging info
    at scale. Failed tables are logged and reported at the end.

    Args:
        tables_info: Dictionary mapping table names to S3 keys
        graph_client: Graph API client instance

    Returns:
        List of successfully created table names

    Raises:
        RuntimeError: If any tables failed to create (after attempting all)
    """
    successful_tables: list[str] = []
    failed_tables: list[tuple[str, str]] = []

    for table_name, s3_keys in tables_info.items():
      logger.info(f"Creating DuckDB table: {table_name} ({len(s3_keys)} files)")

      # Build list of full S3 URIs
      s3_files = [f"s3://{self.bucket}/{key}" for key in s3_keys]

      try:
        # Use graph client to call Graph API's table creation endpoint
        # Client uses SSE monitoring for long-running table creation
        response = await graph_client.create_table(
          graph_id=self.graph_id,
          table_name=table_name,
          s3_pattern=s3_files,  # Actually a list of files, not a pattern
          timeout=1800,  # 30 minutes for large file sets
        )

        # Handle SSE-based response format
        if response.get("status") == "failed":
          error = response.get("error", "Unknown error")
          logger.error(f"Failed to create DuckDB table {table_name}: {error}")
          failed_tables.append((table_name, error))
          continue

        # Extract result from SSE response
        result = response.get("result", {})
        duration = response.get("duration_seconds", result.get("duration_seconds", 0))

        logger.info(
          f"Created DuckDB table {table_name} in {duration:.1f}s "
          f"(from {len(s3_keys)} files)"
        )
        successful_tables.append(table_name)

      except Exception as e:
        logger.error(f"Failed to create DuckDB table {table_name}: {e}")
        failed_tables.append((table_name, str(e)))
        continue

    # Report summary
    if failed_tables:
      logger.warning(
        f"DuckDB table creation: {len(successful_tables)} succeeded, "
        f"{len(failed_tables)} failed"
      )
      for table_name, error in failed_tables:
        logger.error(f"  Failed: {table_name} - {error}")

      # Raise after attempting all tables so we can see partial results
      raise RuntimeError(
        f"Failed to create {len(failed_tables)} DuckDB tables: "
        f"{[t[0] for t in failed_tables]}"
      )

    return successful_tables

  async def _trigger_ingestion(
    self,
    table_names: list[str],
    graph_client,
    rebuild: bool = False,
  ) -> dict[str, Any]:
    """
    Trigger ingestion for all tables into LadybugDB graph via Graph API.

    Args:
        table_names: List of table names to ingest
        graph_client: Graph API client instance
        rebuild: Ignored - rebuild is now handled in process_files before table creation

    Returns:
        Ingestion results with statistics
    """
    total_rows = 0
    total_time_ms = 0.0
    results = []

    for table_name in table_names:
      logger.info(f"Materializing table: {table_name}")

      try:
        response = await graph_client.materialize_table(
          graph_id=self.graph_id,
          table_name=table_name,
          ignore_errors=True,
        )

        total_rows += response.get("rows_ingested", 0)
        total_time_ms += response.get("execution_time_ms", 0)

        results.append(
          {
            "table_name": table_name,
            "rows_ingested": response.get("rows_ingested", 0),
            "status": response.get("status", "success"),
          }
        )

        logger.info(
          f"✓ Materialized {table_name}: "
          f"{response.get('rows_ingested', 0)} rows in "
          f"{response.get('execution_time_ms', 0):.2f}ms"
        )

      except Exception as e:
        logger.error(f"Failed to materialize table {table_name}: {e}")
        results.append(
          {
            "table_name": table_name,
            "status": "error",
            "error": str(e),
          }
        )

    return {
      "total_rows_ingested": total_rows,
      "total_time_ms": total_time_ms,
      "tables": results,
    }
