"""
UUID Utilities for RoboSystems

UUID5 provides deterministic UUIDs based on namespace + content hashing.
Used for XBRL entities that need consistent IDs across pipeline runs.

UUID7 provides time-ordered UUIDs for cases where temporal ordering matters.
"""

import uuid

from uuid6 import uuid7

# Custom namespace UUID for RoboSystems deterministic ID generation
# This ensures our UUIDs don't collide with other systems using UUID5
ROBOSYSTEMS_NAMESPACE = uuid.UUID("a1b2c3d4-e5f6-7890-abcd-ef1234567890")


def generate_uuid7() -> str:
  """
  Generate a time-ordered UUID v7 string.

  Returns:
      str: UUID v7 string (36 chars with hyphens)
  """
  return str(uuid7())


def generate_deterministic_uuid(content: str, namespace: str | None = None) -> str:
  """
  Generate a deterministic UUID based on content using UUID5.

  Uses SHA-1 hashing of a namespace UUID + content to produce the same
  UUID every time for the same input, regardless of process, worker,
  or pipeline run. This ensures consistent entity identification across
  parallel processing and multiple pipeline executions.

  Args:
      content: String to generate ID from
      namespace: Optional namespace to prevent collisions between entity types

  Returns:
      str: Deterministic UUID5 string (always the same for same inputs)
  """
  # Combine namespace with content to prevent collisions between entity types
  # e.g., "entity:https://sec.gov/..." vs "element:https://sec.gov/..."
  full_content = f"{namespace}:{content}" if namespace else content

  # Generate UUID5 using our custom namespace - truly deterministic
  deterministic_id = uuid.uuid5(ROBOSYSTEMS_NAMESPACE, full_content)
  return str(deterministic_id)
