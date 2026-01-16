"""
Tests for UUID utilities.
"""

import time

from robosystems.utils.uuid import (
  generate_deterministic_uuid,
  generate_uuid7,
)


class TestGenerateUuid7:
  """Test basic UUID v7 generation."""

  def test_generate_uuid7_format(self):
    """Test that generated UUID v7 has correct format."""
    uuid_str = generate_uuid7()

    # Should be 36 characters (32 hex + 4 hyphens)
    assert len(uuid_str) == 36

    # Should have proper hyphen placement
    parts = uuid_str.split("-")
    assert len(parts) == 5
    assert len(parts[0]) == 8  # time_hi
    assert len(parts[1]) == 4  # time_mid
    assert len(parts[2]) == 4  # time_low + version
    assert len(parts[3]) == 4  # clock_seq + variant
    assert len(parts[4]) == 12  # node

    # Should be version 7
    assert parts[2].startswith("7")

  def test_generate_uuid7_uniqueness(self):
    """Test that generated UUIDs are unique."""
    uuids = [generate_uuid7() for _ in range(100)]
    assert len(set(uuids)) == 100

  def test_generate_uuid7_ordering(self):
    """Test that UUID v7s are time-ordered."""
    uuid1 = generate_uuid7()
    time.sleep(0.002)  # Small delay to ensure timestamp difference
    uuid2 = generate_uuid7()

    # UUID7s should be lexicographically ordered by time
    assert uuid1 < uuid2


class TestGenerateDeterministicUuid:
  """Test deterministic UUID generation using UUID5 hashing."""

  def test_deterministic_uuid_same_content(self):
    """Test that same content generates same UUID."""
    content = "test-entity-123"

    uuid1 = generate_deterministic_uuid(content)
    uuid2 = generate_deterministic_uuid(content)

    assert uuid1 == uuid2

  def test_deterministic_uuid_different_content(self):
    """Test that different content generates different UUIDs."""
    uuid1 = generate_deterministic_uuid("content-1")
    uuid2 = generate_deterministic_uuid("content-2")

    assert uuid1 != uuid2

  def test_deterministic_uuid_with_namespace(self):
    """Test deterministic UUIDs with namespaces."""
    content = "entity-123"

    uuid1 = generate_deterministic_uuid(content, namespace="users")
    uuid2 = generate_deterministic_uuid(content, namespace="docs")
    uuid3 = generate_deterministic_uuid(content)  # No namespace

    # All should be different due to different namespaces
    assert uuid1 != uuid2
    assert uuid1 != uuid3
    assert uuid2 != uuid3

  def test_deterministic_uuid_consistent_across_calls(self):
    """Test that the same input always produces the same UUID (truly deterministic)."""
    content = "consistent-entity"
    namespace = "test"

    # Multiple calls should always return the exact same value
    uuid1 = generate_deterministic_uuid(content, namespace=namespace)
    uuid2 = generate_deterministic_uuid(content, namespace=namespace)
    uuid3 = generate_deterministic_uuid(content, namespace=namespace)

    assert uuid1 == uuid2 == uuid3

  def test_deterministic_uuid_format(self):
    """Test that deterministic UUIDs have valid UUID5 format."""
    uuid_str = generate_deterministic_uuid("test-content", namespace="test")

    # Should be 36 characters (32 hex + 4 hyphens)
    assert len(uuid_str) == 36

    # Should have proper hyphen placement
    parts = uuid_str.split("-")
    assert len(parts) == 5
    assert len(parts[0]) == 8
    assert len(parts[1]) == 4
    assert len(parts[2]) == 4
    assert len(parts[3]) == 4
    assert len(parts[4]) == 12

    # Should be version 5 (hash-based)
    assert parts[2].startswith("5")


class TestEdgeCases:
  """Test edge cases and error conditions."""

  def test_empty_string_content(self):
    """Test handling of empty string content."""
    uuid_str = generate_deterministic_uuid("")
    assert uuid_str is not None
    assert len(uuid_str) == 36

  def test_unicode_content(self):
    """Test handling of unicode content."""
    unicode_content = "用户-123-测试"
    uuid_str = generate_deterministic_uuid(unicode_content)
    assert uuid_str is not None
    assert len(uuid_str) == 36

  def test_very_long_content(self):
    """Test handling of very long content."""
    long_content = "x" * 10000
    uuid_str = generate_deterministic_uuid(long_content)
    assert uuid_str is not None
    assert len(uuid_str) == 36
