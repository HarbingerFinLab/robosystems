#!/bin/bash
# =============================================================================
# ROBOSYSTEMS BOOTSTRAP SCRIPT
# =============================================================================
#
# Bootstrap using AWS SSO + GitHub OIDC Federation.
# No long-term AWS credentials stored anywhere.
#
# PREREQUISITES:
#   - AWS CLI v2 installed
#   - AWS IAM Identity Center (SSO) enabled with admin access
#   - GitHub CLI installed and authenticated
#
# WHAT THIS DOES:
#   1. Configures AWS CLI SSO profile (if not exists)
#   2. Deploys GitHub OIDC CloudFormation stack
#   3. Sets GitHub variables (role ARN, etc.)
#   4. Creates AWS Secrets Manager secrets
#
# USAGE:
#   just bootstrap
#   or: bin/setup/bootstrap.sh
#
# =============================================================================

set -euo pipefail

# Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
CYAN='\033[0;36m'
NC='\033[0m'

# Configuration
SSO_PROFILE="${AWS_PROFILE:-robosystems-sso}"
OIDC_STACK_NAME="RoboSystemsGitHubOIDC"
AWS_REGION="${AWS_REGION:-us-east-1}"

print_header() {
    echo ""
    echo -e "${CYAN}═══════════════════════════════════════════════════════════${NC}"
    echo -e "${CYAN}  $1${NC}"
    echo -e "${CYAN}═══════════════════════════════════════════════════════════${NC}"
    echo ""
}

print_step() {
    echo -e "${BLUE}▶ $1${NC}"
}

print_success() {
    echo -e "${GREEN}✓ $1${NC}"
}

print_warning() {
    echo -e "${YELLOW}⚠ $1${NC}"
}

print_error() {
    echo -e "${RED}✗ $1${NC}" >&2
}

print_info() {
    echo -e "  $1"
}

# =============================================================================
# SSO CONFIGURATION
# =============================================================================

check_sso_configured() {
    print_header "Checking SSO Configuration"

    # Check if SSO profile exists in config
    if aws configure list-profiles 2>/dev/null | grep -q "^${SSO_PROFILE}$"; then
        print_success "SSO profile '${SSO_PROFILE}' found"
        return 0
    else
        print_warning "SSO profile '${SSO_PROFILE}' not found"
        return 1
    fi
}

configure_sso() {
    print_header "Configure AWS SSO"

    echo "Let's set up AWS CLI SSO access."
    echo ""
    echo "You'll need:"
    echo "  - Your SSO start URL (e.g., https://d-xxxxxxxxxx.awsapps.com/start)"
    echo "  - Your SSO region (usually us-east-1)"
    echo ""

    read -p "SSO Start URL: " SSO_START_URL
    read -p "SSO Region [us-east-1]: " SSO_REGION
    SSO_REGION=${SSO_REGION:-us-east-1}

    echo ""
    print_step "Creating SSO profile '${SSO_PROFILE}'..."

    # Create the config file entry
    mkdir -p ~/.aws

    # Check if profile already exists in config
    if grep -q "\[profile ${SSO_PROFILE}\]" ~/.aws/config 2>/dev/null; then
        print_warning "Profile already exists, updating..."
        # Use a temp file to update
        python3 << EOF
import configparser
config = configparser.ConfigParser()
config.read('$HOME/.aws/config')
section = 'profile ${SSO_PROFILE}'
if not config.has_section(section):
    config.add_section(section)
config.set(section, 'sso_start_url', '${SSO_START_URL}')
config.set(section, 'sso_region', '${SSO_REGION}')
config.set(section, 'sso_registration_scopes', 'sso:account:access')
with open('$HOME/.aws/config', 'w') as f:
    config.write(f)
EOF
    else
        cat >> ~/.aws/config << EOF

[profile ${SSO_PROFILE}]
sso_start_url = ${SSO_START_URL}
sso_region = ${SSO_REGION}
sso_registration_scopes = sso:account:access
EOF
    fi

    print_success "SSO profile created"
    echo ""
    print_step "Starting SSO login (this will open your browser)..."
    echo ""

    aws sso login --profile "${SSO_PROFILE}"

    # After login, we need to select the account and role
    echo ""
    print_step "Fetching available accounts..."

    # Get access token
    local cache_dir="$HOME/.aws/sso/cache"
    local token_file=$(ls -t "$cache_dir"/*.json 2>/dev/null | head -1)

    if [ -z "$token_file" ]; then
        print_error "Could not find SSO token cache"
        exit 1
    fi

    local access_token=$(jq -r '.accessToken' "$token_file")

    # List accounts
    local accounts=$(aws sso list-accounts \
        --access-token "$access_token" \
        --region "${SSO_REGION}" \
        --output json)

    echo ""
    echo "Available accounts:"
    echo "$accounts" | jq -r '.accountList[] | "  \(.accountId) - \(.accountName)"'
    echo ""

    read -p "Enter AWS Account ID to use: " SELECTED_ACCOUNT

    # List roles for the account
    local roles=$(aws sso list-account-roles \
        --access-token "$access_token" \
        --account-id "$SELECTED_ACCOUNT" \
        --region "${SSO_REGION}" \
        --output json)

    echo ""
    echo "Available roles:"
    echo "$roles" | jq -r '.roleList[] | "  \(.roleName)"'
    echo ""

    read -p "Enter role name to use: " SELECTED_ROLE

    # Update the profile with account and role
    python3 << EOF
import configparser
config = configparser.ConfigParser()
config.read('$HOME/.aws/config')
section = 'profile ${SSO_PROFILE}'
config.set(section, 'sso_account_id', '${SELECTED_ACCOUNT}')
config.set(section, 'sso_role_name', '${SELECTED_ROLE}')
config.set(section, 'region', '${AWS_REGION}')
with open('$HOME/.aws/config', 'w') as f:
    config.write(f)
EOF

    print_success "SSO profile configured with account ${SELECTED_ACCOUNT}"

    # Store for later use
    AWS_ACCOUNT_ID="$SELECTED_ACCOUNT"
}

login_sso() {
    print_step "Logging in via SSO..."

    # Check if we have valid credentials
    if aws sts get-caller-identity --profile "${SSO_PROFILE}" &>/dev/null; then
        print_success "Already logged in"
        return 0
    fi

    # Need to login
    print_info "Opening browser for SSO login..."
    aws sso login --profile "${SSO_PROFILE}"

    # Verify login worked
    if aws sts get-caller-identity --profile "${SSO_PROFILE}" &>/dev/null; then
        print_success "SSO login successful"
    else
        print_error "SSO login failed"
        exit 1
    fi
}

# =============================================================================
# GITHUB OIDC DEPLOYMENT
# =============================================================================

deploy_github_oidc() {
    print_header "Deploying GitHub OIDC Federation"

    # Get GitHub org/repo info
    if ! command -v gh &>/dev/null; then
        print_error "GitHub CLI not installed"
        exit 1
    fi

    local repo_info=$(gh repo view --json owner,name 2>/dev/null || echo "")
    if [ -z "$repo_info" ]; then
        print_warning "Not in a GitHub repository"
        read -p "Enter GitHub organization/username: " GITHUB_ORG
        read -p "Enter repository name: " GITHUB_REPO
    else
        GITHUB_ORG=$(echo "$repo_info" | jq -r '.owner.login')
        GITHUB_REPO=$(echo "$repo_info" | jq -r '.name')
        print_info "Detected repository: ${GITHUB_ORG}/${GITHUB_REPO}"
    fi

    echo ""
    read -p "GitHub Organization [${GITHUB_ORG}]: " input_org
    GITHUB_ORG=${input_org:-$GITHUB_ORG}

    echo ""
    print_info "Repository names are hardcoded for security:"
    print_info "Backend role will allow:"
    print_info "  - ${GITHUB_ORG}/robosystems"
    print_info "Frontend role will allow:"
    print_info "  - ${GITHUB_ORG}/robosystems-app"
    print_info "  - ${GITHUB_ORG}/roboledger-app"
    print_info "  - ${GITHUB_ORG}/roboinvestor-app"

    # Branch patterns are hardcoded in the template: main, release/*, v* tags
    echo ""
    print_step "Deploying CloudFormation stack: ${OIDC_STACK_NAME}"

    # Check if stack exists
    local stack_status=""
    stack_status=$(aws cloudformation describe-stacks \
        --stack-name "${OIDC_STACK_NAME}" \
        --profile "${SSO_PROFILE}" \
        --region "${AWS_REGION}" \
        --query 'Stacks[0].StackStatus' \
        --output text 2>/dev/null) || true

    local cf_action="create-stack"
    if [ -n "$stack_status" ]; then
        print_warning "Stack already exists (status: ${stack_status})"
        read -p "Update existing stack? (y/N): " -n 1 -r
        echo ""
        if [[ $REPLY =~ ^[Yy]$ ]]; then
            cf_action="update-stack"
        else
            print_info "Skipping stack deployment"
            # Get existing role ARN
            GITHUB_ACTIONS_ROLE_ARN=$(aws cloudformation describe-stacks \
                --stack-name "${OIDC_STACK_NAME}" \
                --profile "${SSO_PROFILE}" \
                --region "${AWS_REGION}" \
                --query 'Stacks[0].Outputs[?OutputKey==`GitHubActionsRoleArn`].OutputValue' \
                --output text)
            return 0
        fi
    fi

    # Deploy/update stack
    local cf_output=""
    if [ "$cf_action" = "create-stack" ]; then
        aws cloudformation create-stack \
            --stack-name "${OIDC_STACK_NAME}" \
            --template-body file://cloudformation/bootstrap-oidc.yaml \
            --parameters \
                ParameterKey=GitHubOrg,ParameterValue="${GITHUB_ORG}" \
            --capabilities CAPABILITY_NAMED_IAM \
            --profile "${SSO_PROFILE}" \
            --region "${AWS_REGION}" \
            --tags Key=Service,Value=RoboSystems Key=Component,Value=GitHubOIDC

        print_step "Waiting for stack creation..."
        aws cloudformation wait stack-create-complete \
            --stack-name "${OIDC_STACK_NAME}" \
            --profile "${SSO_PROFILE}" \
            --region "${AWS_REGION}"
    else
        aws cloudformation update-stack \
            --stack-name "${OIDC_STACK_NAME}" \
            --template-body file://cloudformation/bootstrap-oidc.yaml \
            --parameters \
                ParameterKey=GitHubOrg,ParameterValue="${GITHUB_ORG}" \
            --capabilities CAPABILITY_NAMED_IAM \
            --profile "${SSO_PROFILE}" \
            --region "${AWS_REGION}" 2>&1 || {
                if [[ $? -eq 255 ]] && aws cloudformation describe-stacks \
                    --stack-name "${OIDC_STACK_NAME}" \
                    --profile "${SSO_PROFILE}" \
                    --region "${AWS_REGION}" &>/dev/null; then
                    print_info "No updates needed"
                else
                    print_error "Stack update failed"
                    exit 1
                fi
            }

        print_step "Waiting for stack update..."
        aws cloudformation wait stack-update-complete \
            --stack-name "${OIDC_STACK_NAME}" \
            --profile "${SSO_PROFILE}" \
            --region "${AWS_REGION}" 2>/dev/null || true
    fi

    # Get outputs
    GITHUB_ACTIONS_ROLE_ARN=$(aws cloudformation describe-stacks \
        --stack-name "${OIDC_STACK_NAME}" \
        --profile "${SSO_PROFILE}" \
        --region "${AWS_REGION}" \
        --query 'Stacks[0].Outputs[?OutputKey==`GitHubActionsRoleArn`].OutputValue' \
        --output text)

    print_success "GitHub OIDC stack deployed"
    print_info "Role ARN: ${GITHUB_ACTIONS_ROLE_ARN}"
}

# =============================================================================
# GITHUB CONFIGURATION
# =============================================================================

configure_github() {
    print_header "Configuring GitHub Repository"

    # Get AWS Account ID
    AWS_ACCOUNT_ID=$(aws sts get-caller-identity \
        --profile "${SSO_PROFILE}" \
        --query 'Account' \
        --output text)

    print_step "Setting GitHub variables for OIDC..."

    # Set the role ARN as a variable (not a secret - it's not sensitive)
    gh variable set AWS_ROLE_ARN --body "${GITHUB_ACTIONS_ROLE_ARN}"
    print_success "Set AWS_ROLE_ARN"

    gh variable set AWS_ACCOUNT_ID --body "${AWS_ACCOUNT_ID}"
    print_success "Set AWS_ACCOUNT_ID"

    gh variable set AWS_REGION --body "${AWS_REGION}"
    print_success "Set AWS_REGION"

    echo ""
    print_step "Note: No AWS secrets needed with OIDC!"
    print_info "Workflows will use role assumption instead of access keys"

    echo ""
    read -p "Run full GitHub variable setup (setup-gha)? (Y/n): " -n 1 -r
    echo ""
    if [[ ! $REPLY =~ ^[Nn]$ ]]; then
        # Run the existing setup script
        ./bin/setup/gha.sh
    fi
}

# =============================================================================
# AWS SECRETS
# =============================================================================

setup_aws_secrets() {
    print_header "AWS Secrets Manager Setup"

    read -p "Create AWS Secrets Manager secrets? (Y/n): " -n 1 -r
    echo ""
    if [[ $REPLY =~ ^[Nn]$ ]]; then
        print_info "Skipping secrets setup"
        return 0
    fi

    # Export profile for the aws.sh script
    export AWS_PROFILE="${SSO_PROFILE}"

    # Run the existing setup script
    ./bin/setup/aws.sh
}

# =============================================================================
# CHECK GITHUB SECRETS
# =============================================================================

check_github_secrets() {
    print_header "Checking GitHub Secrets (Repo-Level)"

    print_step "Checking for required secrets..."

    # Get list of secrets
    SECRETS=$(gh secret list 2>/dev/null || echo "")

    # Check for ACTIONS_TOKEN
    if echo "$SECRETS" | grep -q "ACTIONS_TOKEN"; then
        print_success "ACTIONS_TOKEN exists"
    else
        print_warning "ACTIONS_TOKEN not found"
        echo ""
        echo "  This secret is required for workflow automation."
        echo "  Create a GitHub Personal Access Token with 'repo' and 'workflow' scopes."
        echo ""
        echo "  To set it:"
        echo "    gh secret set ACTIONS_TOKEN"
        echo ""
        MISSING_SECRETS=true
    fi

    # Check for optional secrets
    if echo "$SECRETS" | grep -q "ANTHROPIC_API_KEY"; then
        print_success "ANTHROPIC_API_KEY exists (optional)"
    else
        print_info "ANTHROPIC_API_KEY not set (optional - enables AI features)"
    fi

    echo ""
    print_info "Note: AWS credentials (access keys) are NOT needed with OIDC"
    print_info "Workflows authenticate via AWS_ROLE_ARN instead"

    if [ "${MISSING_SECRETS:-false}" = "true" ]; then
        echo ""
        print_warning "Some required secrets are missing. Set them before deploying."
    fi
}

# =============================================================================
# SUMMARY
# =============================================================================

show_summary() {
    print_header "Bootstrap Complete!"

    echo -e "${GREEN}════════════════════════════════════════════════════════════${NC}"
    echo -e "${GREEN}  BOOTSTRAP COMPLETED SUCCESSFULLY${NC}"
    echo -e "${GREEN}════════════════════════════════════════════════════════════${NC}"
    echo ""
    echo -e "${CYAN}AWS Profile:${NC} ${SSO_PROFILE}"
    echo -e "${CYAN}AWS Account:${NC} ${AWS_ACCOUNT_ID}"
    echo -e "${CYAN}AWS Region:${NC} ${AWS_REGION}"
    echo ""
    echo -e "${CYAN}GitHub OIDC Role:${NC}"
    echo "  ${GITHUB_ACTIONS_ROLE_ARN}"
    echo ""
    echo -e "${CYAN}════════════════════════════════════════════════════════════${NC}"
    echo -e "${CYAN}  SECURITY BENEFITS${NC}"
    echo -e "${CYAN}════════════════════════════════════════════════════════════${NC}"
    echo ""
    echo "  ✓ No long-term credentials stored anywhere"
    echo "  ✓ Credentials scoped to specific repo/branch"
    echo "  ✓ 1-hour max session (can't be abused if compromised)"
    echo ""
    echo -e "${CYAN}════════════════════════════════════════════════════════════${NC}"
    echo -e "${CYAN}  NEXT STEPS${NC}"
    echo -e "${CYAN}════════════════════════════════════════════════════════════${NC}"
    echo ""
    echo "1. Deploy to production:"
    echo "   just deploy prod"
    echo ""
    echo "2. For CLI access, use:"
    echo "   aws sso login --profile ${SSO_PROFILE}"
    echo "   export AWS_PROFILE=${SSO_PROFILE}"
    echo ""
}

# =============================================================================
# MAIN
# =============================================================================

main() {
    print_header "RoboSystems Bootstrap"

    echo "This script sets up AWS access for GitHub Actions:"
    echo ""
    echo "  • AWS CLI via SSO (temporary credentials)"
    echo "  • GitHub Actions via OIDC (no stored secrets)"
    echo ""
    echo "Prerequisites:"
    echo "  ✓ AWS IAM Identity Center enabled"
    echo "  ✓ SSO admin account exists"
    echo "  ✓ GitHub CLI installed and authenticated"
    echo ""

    # Check prerequisites
    if ! command -v aws &>/dev/null; then
        print_error "AWS CLI not installed"
        exit 1
    fi

    if ! command -v gh &>/dev/null; then
        print_error "GitHub CLI not installed"
        exit 1
    fi

    if ! command -v jq &>/dev/null; then
        print_error "jq not installed"
        exit 1
    fi

    if ! gh auth status &>/dev/null; then
        print_error "GitHub CLI not authenticated. Run: gh auth login"
        exit 1
    fi

    read -p "Continue with bootstrap? (y/N): " -n 1 -r
    echo ""
    if [[ ! $REPLY =~ ^[Yy]$ ]]; then
        print_info "Bootstrap cancelled"
        exit 0
    fi

    # Check/configure SSO
    if ! check_sso_configured; then
        configure_sso
    else
        login_sso
    fi

    # Get account ID for later
    AWS_ACCOUNT_ID=$(aws sts get-caller-identity \
        --profile "${SSO_PROFILE}" \
        --query 'Account' \
        --output text)

    # Deploy GitHub OIDC
    deploy_github_oidc

    # Configure GitHub
    configure_github

    # Check GitHub secrets
    check_github_secrets

    # Setup AWS secrets
    setup_aws_secrets

    # Summary
    show_summary
}

main "$@"
