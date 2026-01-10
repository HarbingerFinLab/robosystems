"""
S3 bucket name computation helper.

Centralized logic for computing S3 bucket names to avoid drift between:
- robosystems/config/env.py (static bucket names at import time)
- robosystems/config/secrets_manager.py (get_s3_buckets method)
- cloudformation/s3.yaml (CloudFormation template)

CloudFormation has its own logic using !If conditions, but the Python
implementations should use this shared helper.
"""


def compute_bucket_prefix(namespace: str = "") -> str:
  """
  Compute the bucket name prefix.

  Args:
      namespace: Optional namespace (AWS account ID for forks, empty for main deployment)

  Returns:
      Prefix like "robosystems" or "robosystems-123456789012"
  """
  return f"robosystems-{namespace}" if namespace else "robosystems"


def compute_bucket_suffix(environment: str) -> str:
  """
  Compute the bucket name suffix based on environment.

  Args:
      environment: Environment name (dev, staging, prod)

  Returns:
      Empty string for dev, otherwise "-{environment}"
  """
  return "" if environment == "dev" else f"-{environment}"


def compute_bucket_name(
  purpose: str, namespace: str = "", environment: str = "dev"
) -> str:
  """
  Compute a complete S3 bucket name.

  Args:
      purpose: Bucket purpose (shared-raw, shared-processed, user, public-data, deployment, logs)
      namespace: Optional namespace (AWS account ID for forks)
      environment: Environment name (dev, staging, prod)

  Returns:
      Complete bucket name like:
      - "robosystems-shared-raw" (dev, no namespace)
      - "robosystems-shared-raw-staging" (staging, no namespace)
      - "robosystems-123456789012-shared-raw-prod" (prod, with namespace)
  """
  prefix = compute_bucket_prefix(namespace)
  suffix = compute_bucket_suffix(environment)
  return f"{prefix}-{purpose}{suffix}"


def get_all_bucket_names(
  namespace: str = "", environment: str = "dev"
) -> dict[str, str]:
  """
  Get all bucket names for a given namespace and environment.

  Args:
      namespace: Optional namespace (AWS account ID for forks)
      environment: Environment name (dev, staging, prod)

  Returns:
      Dictionary mapping bucket purposes to full bucket names
  """
  prefix = compute_bucket_prefix(namespace)
  suffix = compute_bucket_suffix(environment)

  return {
    "shared_raw": f"{prefix}-shared-raw{suffix}",
    "shared_processed": f"{prefix}-shared-processed{suffix}",
    "user_data": f"{prefix}-user{suffix}",
    "public_data": f"{prefix}-public-data{suffix}",
    "deployment": f"{prefix}-deployment{suffix}",
    "logs": f"{prefix}-logs{suffix}",
  }
