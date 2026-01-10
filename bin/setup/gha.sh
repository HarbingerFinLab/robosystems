#!/bin/bash
# =============================================================================
# ROBOSYSTEMS SERVICE GITHUB REPOSITORY SETUP SCRIPT
# =============================================================================
#
# This script configures GitHub repository secrets and variables used by CI/CD
# pipelines and deployment automation.
#
# Usage:
#   just setup-gha
#   or directly: bin/setup/gha
#
# Required GitHub repository configuration:
# - Repository secrets (sensitive data)
# - Repository variables (non-sensitive configuration)
#
# =============================================================================

set -euo pipefail

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# Function to print colored output
print_error() {
    echo -e "${RED}❌ $1${NC}" >&2
}

print_info() {
    echo -e "${BLUE}ℹ️  $1${NC}"
}

print_success() {
    echo -e "${GREEN}✅ $1${NC}"
}

print_warning() {
    echo -e "${YELLOW}⚠️  $1${NC}"
}

# Function to look up latest Amazon Linux 2023 ARM64 AMI from AWS SSM
get_latest_ami() {
    local ssm_path="/aws/service/ami-amazon-linux-latest/al2023-ami-kernel-default-arm64"

    # Check if AWS CLI is available and authenticated
    if ! command -v aws >/dev/null 2>&1; then
        echo ""
        return 0  # Return success to avoid set -e exit, caller checks empty string
    fi

    # Try to get the AMI ID from SSM Parameter Store
    # Use || true to prevent set -e from exiting on AWS CLI failure
    local ami_id
    ami_id=$(aws ssm get-parameter \
        --name "$ssm_path" \
        --query "Parameter.Value" \
        --output text 2>/dev/null) || true

    if [ -n "$ami_id" ] && [ "$ami_id" != "None" ]; then
        echo "$ami_id"
        return 0
    fi

    echo ""
    return 0  # Return success to avoid set -e exit, caller checks empty string
}

echo "=== RoboSystems GitHub Repository Setup ==="
echo ""


# =============================================================================
# GITHUB SETUP FUNCTIONS
# =============================================================================

function check_prerequisites() {
    print_info "Checking prerequisites..."

    # Check GitHub CLI
    if ! command -v gh >/dev/null 2>&1; then
        print_error "GitHub CLI is not installed. Please install it first."
        echo "   Visit: https://cli.github.com/"
        exit 1
    fi

    # Check GitHub authentication
    if ! gh auth status >/dev/null 2>&1; then
        print_error "GitHub CLI not authenticated."
        echo "   Run: gh auth login"
        exit 1
    fi

    # Check if we're in a git repository
    if ! git rev-parse --is-inside-work-tree >/dev/null 2>&1; then
        print_error "Not in a git repository"
        exit 1
    fi

    # Get repository name
    REPO_NAME=$(gh repo view --json nameWithOwner -q .nameWithOwner 2>/dev/null || echo "")
    if [ -z "$REPO_NAME" ]; then
        print_error "Could not determine repository name"
        exit 1
    fi

    print_success "Prerequisites check passed"
    print_info "Repository: $REPO_NAME"
    echo ""
}

function show_optional_secrets() {
    echo "📋 Optional Secrets (not required for deployment):"
    echo ""
    echo "   ACTIONS_TOKEN      - Enables cross-workflow triggers, auto-deploy on release"
    echo "   ANTHROPIC_API_KEY  - Enables AI-powered PR summaries and release notes"
    echo ""
    echo "To set secrets:"
    echo "   gh secret set ACTIONS_TOKEN"
    echo "   gh secret set ANTHROPIC_API_KEY"
    echo ""
    echo "Note: AWS credentials are handled via OIDC (no secrets needed)."
}


function setup_full_config() {
    echo "Setting up full configuration with all currently used variables..."
    echo ""

    # Domain configuration (optional for VPC-only deployments)
    echo "📋 Domain Configuration:"
    echo "   Leave empty for VPC-only deployment (access via bastion tunnel)"
    while true; do
        read -p "Enter Root Domain (e.g., robosystems.ai) or press Enter to skip: " ROOT_DOMAIN
        # Allow empty for VPC-only deployment
        if [ -z "$ROOT_DOMAIN" ]; then
            print_info "No domain configured - API will be accessible via bastion tunnel only"
            break
        fi
        # Basic domain validation: must contain at least one dot and valid characters
        if [[ "$ROOT_DOMAIN" =~ ^[a-zA-Z0-9]([a-zA-Z0-9-]*[a-zA-Z0-9])?(\.[a-zA-Z0-9]([a-zA-Z0-9-]*[a-zA-Z0-9])?)+$ ]]; then
            break
        else
            echo "❌ Invalid domain format. Please enter a valid domain (e.g., example.com) or press Enter to skip"
        fi
    done
    read -p "Enter GitHub Organization Name [YourGitHubOrg]: " GITHUB_ORG
    GITHUB_ORG=${GITHUB_ORG:-"YourGitHubOrg"}
    read -p "Enter Repository Name [robosystems-service]: " REPO_NAME
    REPO_NAME=${REPO_NAME:-"robosystems-service"}
    REPOSITORY_NAME="${GITHUB_ORG}/${REPO_NAME}"
    read -p "Enter AWS Account ID: " AWS_ACCOUNT_ID
    read -p "Enter AWS SNS Alert Email: " AWS_SNS_ALERT_EMAIL
    read -p "Enter ECR Repository Name [robosystems]: " ECR_REPOSITORY
    ECR_REPOSITORY=${ECR_REPOSITORY:-"robosystems"}

    echo ""
    echo "Setting all variables..."

    # Core Infrastructure
    gh variable set REPOSITORY_NAME --body "$REPOSITORY_NAME"
    gh variable set ECR_REPOSITORY --body "$ECR_REPOSITORY"

    # AWS Configuration (typically org-level, set at repo level for forks)
    gh variable set AWS_ACCOUNT_ID --body "$AWS_ACCOUNT_ID"
    gh variable set AWS_REGION --body "us-east-1"
    gh variable set ENVIRONMENT_PROD --body "prod"
    gh variable set ENVIRONMENT_STAGING --body "staging"

    # Domain Configuration (skip for VPC-only deployment - workflows default to empty)
    if [ -n "$ROOT_DOMAIN" ]; then
        gh variable set API_DOMAIN_NAME_ROOT --body "$ROOT_DOMAIN"
        gh variable set API_DOMAIN_NAME_PROD --body "api.$ROOT_DOMAIN"
        gh variable set API_DOMAIN_NAME_STAGING --body "staging.api.$ROOT_DOMAIN"
        gh variable set ROBOSYSTEMS_API_URL_PROD --body "https://api.$ROOT_DOMAIN"
        gh variable set ROBOSYSTEMS_API_URL_STAGING --body "https://staging.api.$ROOT_DOMAIN"
        gh variable set ROBOSYSTEMS_APP_URL_PROD --body "https://$ROOT_DOMAIN"
        gh variable set ROBOSYSTEMS_APP_URL_STAGING --body "https://staging.$ROOT_DOMAIN"
    fi
    # VPC-only: domain variables not set, workflows use || '' fallbacks

    # Admin API access (set to your IP for restricted access)
    gh variable set ADMIN_ALLOWED_CIDRS --body "0.0.0.0/32"

    # API Scaling Configuration
    gh variable set API_MIN_CAPACITY_PROD --body "1"
    gh variable set API_MAX_CAPACITY_PROD --body "10"
    gh variable set API_MIN_CAPACITY_STAGING --body "1"
    gh variable set API_MAX_CAPACITY_STAGING --body "2"
    gh variable set API_ASG_REFRESH_PROD --body "true"
    gh variable set API_ASG_REFRESH_STAGING --body "true"

    # Dagster Daemon Configuration
    gh variable set DAGSTER_DAEMON_CPU_PROD --body "1024"
    gh variable set DAGSTER_DAEMON_CPU_STAGING --body "1024"
    gh variable set DAGSTER_DAEMON_MEMORY_PROD --body "2048"
    gh variable set DAGSTER_DAEMON_MEMORY_STAGING --body "2048"

    # Dagster Webserver Configuration
    gh variable set DAGSTER_WEBSERVER_CPU_PROD --body "512"
    gh variable set DAGSTER_WEBSERVER_CPU_STAGING --body "512"
    gh variable set DAGSTER_WEBSERVER_MEMORY_PROD --body "1024"
    gh variable set DAGSTER_WEBSERVER_MEMORY_STAGING --body "1024"

    # Dagster Run Job Configuration (EcsRunLauncher - Fargate)
    gh variable set DAGSTER_RUN_JOB_CPU_PROD --body "1024"
    gh variable set DAGSTER_RUN_JOB_CPU_STAGING --body "1024"
    gh variable set DAGSTER_RUN_JOB_MEMORY_PROD --body "4096"
    gh variable set DAGSTER_RUN_JOB_MEMORY_STAGING --body "4096"
    gh variable set DAGSTER_MAX_CONCURRENT_RUNS_PROD --body "20"
    gh variable set DAGSTER_MAX_CONCURRENT_RUNS_STAGING --body "20"

    # Dagster Deployment Options
    gh variable set DAGSTER_REFRESH_ECS_PROD --body "true"
    gh variable set DAGSTER_REFRESH_ECS_STAGING --body "true"
    gh variable set RUN_MIGRATIONS_PROD --body "true"
    gh variable set RUN_MIGRATIONS_STAGING --body "true"

    # Dagster Monitoring Configuration
    gh variable set DAGSTER_CONTAINER_INSIGHTS_PROD --body "disabled"
    gh variable set DAGSTER_CONTAINER_INSIGHTS_STAGING --body "disabled"

    # Database Configuration
    gh variable set DATABASE_ENGINE_PROD --body "postgres"
    gh variable set DATABASE_ENGINE_STAGING --body "postgres"
    gh variable set DATABASE_INSTANCE_SIZE_PROD --body "db.t4g.small"
    gh variable set DATABASE_INSTANCE_SIZE_STAGING --body "db.t4g.small"
    gh variable set DATABASE_ALLOCATED_STORAGE_PROD --body "20"
    gh variable set DATABASE_ALLOCATED_STORAGE_STAGING --body "20"
    gh variable set DATABASE_MAX_ALLOCATED_STORAGE_PROD --body "100"
    gh variable set DATABASE_MAX_ALLOCATED_STORAGE_STAGING --body "100"
    gh variable set DATABASE_MULTI_AZ_ENABLED_PROD --body "false"
    gh variable set DATABASE_MULTI_AZ_ENABLED_STAGING --body "false"
    gh variable set DATABASE_SECRETS_ROTATION_DAYS --body "90"

    # Database Versions (pin to override template defaults)
    gh variable set DATABASE_POSTGRES_VERSION_PROD --body "16.11"
    gh variable set DATABASE_POSTGRES_VERSION_STAGING --body "16.11"

    # VPC Flow Logs Configuration (SOC 2 - VPC-level, not environment-specific)
    gh variable set VPC_FLOW_LOGS_ENABLED --body "true"
    gh variable set VPC_FLOW_LOGS_RETENTION_DAYS --body "90"
    gh variable set VPC_FLOW_LOGS_TRAFFIC_TYPE --body "REJECT"

    # CloudTrail Configuration (SOC 2 - Account-level, not environment-specific)
    gh variable set CLOUDTRAIL_ENABLED --body "true"
    gh variable set CLOUDTRAIL_LOG_RETENTION_DAYS --body "90"
    gh variable set CLOUDTRAIL_DATA_EVENTS_ENABLED --body "false"

    # Valkey Configuration
    gh variable set VALKEY_NODE_TYPE_PROD --body "cache.t4g.micro"
    gh variable set VALKEY_NODE_TYPE_STAGING --body "cache.t4g.micro"
    gh variable set VALKEY_NUM_NODES_PROD --body "1"
    gh variable set VALKEY_NUM_NODES_STAGING --body "1"
    gh variable set VALKEY_ENCRYPTION_ENABLED_PROD --body "true"
    gh variable set VALKEY_ENCRYPTION_ENABLED_STAGING --body "true"
    gh variable set VALKEY_SECRET_ROTATION_ENABLED_PROD --body "true"
    gh variable set VALKEY_SECRET_ROTATION_ENABLED_STAGING --body "true"
    gh variable set VALKEY_ROTATION_SCHEDULE_DAYS_PROD --body "90"
    gh variable set VALKEY_ROTATION_SCHEDULE_DAYS_STAGING --body "90"
    gh variable set VALKEY_SNAPSHOT_RETENTION_DAYS_PROD --body "7"
    gh variable set VALKEY_SNAPSHOT_RETENTION_DAYS_STAGING --body "0"

    gh variable set VALKEY_VERSION_PROD --body "8.1"
    gh variable set VALKEY_VERSION_STAGING --body "8.1"

    # LadybugDB Writer Configuration - Standard Tier
    gh variable set LBUG_STANDARD_ENABLED_PROD --body "true"
    gh variable set LBUG_STANDARD_ENABLED_STAGING --body "true"
    gh variable set LBUG_STANDARD_MIN_INSTANCES_PROD --body "1"
    gh variable set LBUG_STANDARD_MAX_INSTANCES_PROD --body "10"
    gh variable set LBUG_STANDARD_MIN_INSTANCES_STAGING --body "1"
    gh variable set LBUG_STANDARD_MAX_INSTANCES_STAGING --body "5"

    # LadybugDB Writer Configuration - Large Tier
    gh variable set LBUG_LARGE_ENABLED_PROD --body "false"
    gh variable set LBUG_LARGE_ENABLED_STAGING --body "false"
    gh variable set LBUG_LARGE_MIN_INSTANCES_PROD --body "0"
    gh variable set LBUG_LARGE_MAX_INSTANCES_PROD --body "20"
    gh variable set LBUG_LARGE_MIN_INSTANCES_STAGING --body "0"
    gh variable set LBUG_LARGE_MAX_INSTANCES_STAGING --body "5"

    # LadybugDB Writer Configuration - XLarge Tier
    gh variable set LBUG_XLARGE_ENABLED_PROD --body "false"
    gh variable set LBUG_XLARGE_ENABLED_STAGING --body "false"
    gh variable set LBUG_XLARGE_MIN_INSTANCES_PROD --body "0"
    gh variable set LBUG_XLARGE_MAX_INSTANCES_PROD --body "10"
    gh variable set LBUG_XLARGE_MIN_INSTANCES_STAGING --body "0"
    gh variable set LBUG_XLARGE_MAX_INSTANCES_STAGING --body "5"

    # LadybugDB Writer Configuration - Shared Repository
    gh variable set LBUG_SHARED_ENABLED_PROD --body "true"
    gh variable set LBUG_SHARED_ENABLED_STAGING --body "true"
    gh variable set LBUG_SHARED_MIN_INSTANCES_PROD --body "1"
    gh variable set LBUG_SHARED_MAX_INSTANCES_PROD --body "3"
    gh variable set LBUG_SHARED_MIN_INSTANCES_STAGING --body "1"
    gh variable set LBUG_SHARED_MAX_INSTANCES_STAGING --body "2"

    # Neo4j Writer Configuration (optional backend)
    gh variable set NEO4J_COMMUNITY_LARGE_ENABLED_PROD --body "false"
    gh variable set NEO4J_COMMUNITY_LARGE_ENABLED_STAGING --body "false"
    gh variable set NEO4J_ENTERPRISE_XLARGE_ENABLED_PROD --body "false"
    gh variable set NEO4J_ENTERPRISE_XLARGE_ENABLED_STAGING --body "false"

    # Graph AMI Configuration (updated via Graph Maintenance workflow)
    # Look up latest Amazon Linux 2023 ARM64 AMI from AWS SSM
    print_info "Looking up latest Amazon Linux 2023 ARM64 AMI..."
    LATEST_AMI=$(get_latest_ami)
    if [ -n "$LATEST_AMI" ]; then
        print_success "Found latest AMI: $LATEST_AMI"
        gh variable set GRAPH_AMI_ID_PROD --body "$LATEST_AMI"
        gh variable set GRAPH_AMI_ID_STAGING --body "$LATEST_AMI"
    else
        print_warning "Could not look up latest AMI from AWS SSM (requires AWS CLI auth)"
        print_warning "Skipping GRAPH_AMI_ID_* - set manually or run graph-maintenance workflow"
    fi
    # Opt-in: set to "true" to enable monthly scheduled AMI checks
    # gh variable set GRAPH_AMI_AUTO_UPDATE --body "true"
    # Opt-in: set to "true" to also trigger deploy when scheduled AMI update finds a new AMI
    # gh variable set GRAPH_AMI_AUTO_DEPLOY --body "true"

    # Graph Settings
    gh variable set GRAPH_API_KEY_ROTATION_DAYS --body "90"
    gh variable set GRAPH_UPDATE_CONTAINERS_PROD --body "true"
    gh variable set GRAPH_UPDATE_CONTAINERS_STAGING --body "true"

    # GitHub Actions Runner Configuration
    # Default: "github-hosted" uses GitHub-hosted runners (ubuntu-latest)
    # For self-hosted: set RUNNER_LABELS to e.g. "self-hosted,Linux,X64"
    # RUNNER_SCOPE: "repo" (check repo only), "org" (org only), "both" (repo then org)
    gh variable set RUNNER_LABELS --body "github-hosted"
    gh variable set RUNNER_SCOPE --body "both"

    # Notification Configuration
    gh variable set AWS_SNS_ALERT_EMAIL --body "$AWS_SNS_ALERT_EMAIL"

    # Features Configuration
    gh variable set OBSERVABILITY_ENABLED_PROD --body "true"
    gh variable set OBSERVABILITY_ENABLED_STAGING --body "true"

    # WAF Configuration (environment-specific)
    gh variable set WAF_ENABLED_PROD --body "true"
    gh variable set WAF_ENABLED_STAGING --body "true"
    gh variable set WAF_RATE_LIMIT_PER_IP --body "10000"
    gh variable set WAF_GEO_BLOCKING_ENABLED --body "false"
    gh variable set WAF_AWS_MANAGED_RULES_ENABLED --body "true"

    # Infrastructure Configuration
    gh variable set MAX_AVAILABILITY_ZONES --body "5"

    # Public Domain Configuration (optional for frontend apps, skip if no domain)
    if [ -n "$ROOT_DOMAIN" ]; then
        gh variable set PUBLIC_DOMAIN_NAME_PROD --body "public.$ROOT_DOMAIN"
        gh variable set PUBLIC_DOMAIN_NAME_STAGING --body "public-staging.$ROOT_DOMAIN"
    fi

    # Additional Application URLs (optional, for multi-app ecosystems)
    # Only offer if domain is configured
    if [ -n "$ROOT_DOMAIN" ]; then
        echo ""
        read -p "Configure RoboLedger app URLs? (y/N): " -n 1 -r
        echo ""
        if [[ $REPLY =~ ^[Yy]$ ]]; then
            read -p "RoboLedger domain (e.g., roboledger.ai): " ROBOLEDGER_DOMAIN
            gh variable set ROBOLEDGER_APP_URL_PROD --body "https://$ROBOLEDGER_DOMAIN"
            gh variable set ROBOLEDGER_APP_URL_STAGING --body "https://staging.$ROBOLEDGER_DOMAIN"
        fi

        read -p "Configure RoboInvestor app URLs? (y/N): " -n 1 -r
        echo ""
        if [[ $REPLY =~ ^[Yy]$ ]]; then
            read -p "RoboInvestor domain (e.g., roboinvestor.ai): " ROBOINVESTOR_DOMAIN
            gh variable set ROBOINVESTOR_APP_URL_PROD --body "https://$ROBOINVESTOR_DOMAIN"
            gh variable set ROBOINVESTOR_APP_URL_STAGING --body "https://staging.$ROBOINVESTOR_DOMAIN"
        fi
    fi

    # Publishing Configuration
    gh variable set DOCKERHUB_PUBLISHING_ENABLED --body "false"

    echo ""
    echo "✅ Full configuration completed!"
    echo ""
    echo "📋 Summary of configured variables:"
    if [ -n "$ROOT_DOMAIN" ]; then
        echo "  🌐 Domains: api.$ROOT_DOMAIN, staging.api.$ROOT_DOMAIN"
    else
        echo "  🌐 Domain: VPC-only (bastion tunnel access)"
    fi
    echo "  📦 Repository: $REPOSITORY_NAME"
    echo "  🐳 ECR: $ECR_REPOSITORY"
    echo "  🔧 Total variables configured: 81"
    echo ""
    echo "All variables have been set to their current production defaults."
}

# =============================================================================
# MAIN SCRIPT EXECUTION
# =============================================================================

function main() {
    check_prerequisites

    echo "This script will configure GitHub repository secrets and variables."
    echo ""

    # Show current repository
    local repo_info=$(gh repo view --json nameWithOwner --jq '.nameWithOwner' 2>/dev/null || echo "Unknown")
    echo "Repository: $repo_info"
    echo ""

    echo "This sets ~80 GitHub variables for full control over infrastructure."
    echo "Note: Basic deployments work without this (workflows have sensible defaults)."
    echo ""
    read -p "Continue with full variable setup? (Y/n): " -n 1 -r
    echo ""
    if [[ $REPLY =~ ^[Nn]$ ]]; then
        echo "Cancelled."
        exit 0
    fi
    echo ""

    setup_full_config
    echo ""
    show_optional_secrets

    echo ""
    echo "✅ GitHub repository setup completed!"
    echo ""
    echo "📋 Next steps:"
    echo "   1. Deploy: just deploy staging"
    echo "   2. Verify variables: gh variable list"
}

# Run main function if script is executed directly
if [[ "${BASH_SOURCE[0]}" == "${0}" ]]; then
    main "$@"
fi
