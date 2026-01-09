# Grafana Dashboard Templates

Portable Grafana dashboard exports with templated datasource UIDs for easy import into any Grafana instance.

## Files

| File | Description |
|------|-------------|
| `prod.json` | Production environment monitoring (metrics) |
| `staging.json` | Staging environment monitoring (metrics) |
| `prod_logs.json` | Production CloudWatch logs |
| `staging_logs.json` | Staging CloudWatch logs |
| `cur.json` | AWS Cost and Usage Report dashboard |

## Template Variables

These dashboards use Grafana's `${DS_*}` variable syntax for datasources:

| Variable | Type | Description |
|----------|------|-------------|
| `${DS_PROMETHEUS}` | Prometheus | Amazon Managed Prometheus |
| `${DS_CLOUDWATCH}` | CloudWatch | AWS CloudWatch metrics |
| `${DS_ATHENA}` | Athena | AWS Athena (CUR dashboard only) |
| `${datasource}` | CloudWatch | Dashboard variable (logs dashboards) |

When importing, Grafana will prompt you to map these to your actual datasources.

## Usage

1. Open Grafana workspace
2. Go to Dashboards > Import
3. Upload or paste the JSON
4. Map datasources when prompted

## Datasources Required

- **Prometheus**: Amazon Managed Prometheus workspace
- **CloudWatch**: AWS CloudWatch (usually auto-configured)
- **Athena**: For CUR dashboard - requires CUR with Athena integration

## CUR Setup (Cost and Usage Reports)

The `cur.json` dashboard requires AWS Cost and Usage Reports configured with Athena integration.

### Setup Steps

1. Go to **AWS Billing Console** > **Cost & Usage Reports**
2. Create a new report with these settings:
   - Enable **Athena integration** (creates Glue database automatically)
3. AWS generates a CloudFormation template - run it to create:
   - Glue database
   - Glue crawler for automatic table updates
   - Lambda triggers for S3 notifications
4. Configure Athena datasource in Grafana pointing to the Glue database

### Required Tags for Cost Allocation

Ensure AWS resources are tagged for the dashboard filters:
- `user:component` - Component identifier (e.g., `api`, `worker`, `ladybug`)
- `user:environment` - Environment name (e.g., `prod`, `staging`)

## Updating Dashboards

When exporting updated dashboards from Grafana:

1. Open dashboard > Settings (gear icon) > JSON Model
2. Copy JSON and save to this directory
3. Replace hardcoded datasource UIDs with template variables:
   - Prometheus UID → `${DS_PROMETHEUS}`
   - CloudWatch UID → `${DS_CLOUDWATCH}`
   - Athena UID → `${DS_ATHENA}`
4. Set root `id` and `uid` to `null`
