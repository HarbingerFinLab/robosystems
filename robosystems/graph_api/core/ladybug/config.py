"""LadybugDB configuration utilities.

This module provides configuration functions that need to be imported
by multiple modules in the ladybug package without circular imports.
"""

from robosystems.config import env
from robosystems.logger import logger


def get_database_memory_config() -> int:
  """
  Get memory configuration in MB for LadybugDB database creation.

  This function provides a single source of truth for memory allocation,
  used by both the connection pool and database manager.

  Priority order:
  1. Per-database memory limit (memory_per_db_mb) - for standard tier with oversubscription
  2. Total memory allocation (lbug_max_memory_mb or max_memory_mb) - for dedicated instances
  3. Environment variable fallback (LBUG_MAX_MEMORY_MB)

  Returns:
      Memory allocation in megabytes
  """
  tier_config = env.get_lbug_tier_config()
  memory_per_db_mb = tier_config.get("memory_per_db_mb", 0)

  if memory_per_db_mb > 0:
    # Use per-database limit (for standard tier with oversubscription)
    logger.info(f"Using per-database memory limit: {memory_per_db_mb} MB")
    return memory_per_db_mb

  # Fall back to total memory for single-database instances (shared/dedicated)
  max_memory_mb = tier_config.get(
    "lbug_max_memory_mb", tier_config.get("max_memory_mb", env.LBUG_MAX_MEMORY_MB)
  )
  logger.info(
    f"Using total memory allocation: {max_memory_mb} MB (tier: {tier_config.get('tier', 'default')})"
  )
  return max_memory_mb
