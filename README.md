# Trino on Kubernetes with Pulumi

Deploy Trino, MinIO, PostgreSQL, and Hive Metastore to a local Kubernetes cluster using Pulumi.

## Architecture

- **Trino** - Distributed SQL query engine (1 coordinator + 3 workers)
- **MinIO** - S3-compatible object storage for Iceberg data
- **PostgreSQL 17** - Metadata storage for Hive Metastore
- **Apache Hive Metastore 4.0.1** - Iceberg catalog backend

## Prerequisites

### 1. Install Homebrew (macOS)

```bash
/bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"
echo 'eval "$(/opt/homebrew/bin/brew shellenv)"' >> ~/.zprofile
eval "$(/opt/homebrew/bin/brew shellenv)"
```

### 2. Install Docker Desktop

Download and install from [docker.com](https://www.docker.com/products/docker-desktop/)

### 3. Install Required Tools

```bash
brew install kind kubectl helm pulumi uv
```

### 4. Configure Pulumi for Local State

```bash
pulumi login file://~
```

This stores Pulumi state locally instead of in Pulumi Cloud.

### 5. Create the Kind Cluster

Create a file `kind-config.yaml` in the parent directory:

```yaml
kind: Cluster
apiVersion: kind.x-k8s.io/v1alpha4
name: trino
nodes:
  - role: control-plane
    extraPortMappings:
      - containerPort: 30080
        hostPort: 8080   # Trino
      - containerPort: 30900
        hostPort: 9000   # MinIO API
      - containerPort: 30901
        hostPort: 9001   # MinIO Console
  - role: worker
  - role: worker
  - role: worker
```

Create the cluster:

```bash
kind create cluster --config ../kind-config.yaml
```

Verify the cluster is running:

```bash
kubectl get nodes
```

### 6. Install Python Dependencies

```bash
cd pulumi-trino
uv sync
```

### 7. Initialize Pulumi Stack

If this is a fresh clone, initialize the dev stack:

```bash
PULUMI_CONFIG_PASSPHRASE="" pulumi stack init dev
```

### 8. Set Required Secrets

```bash
PULUMI_CONFIG_PASSPHRASE="" pulumi config set --secret minioPassword "your-minio-password"
PULUMI_CONFIG_PASSPHRASE="" pulumi config set --secret pgPassword "your-postgres-password"
```

## Deploy

```bash
PULUMI_CONFIG_PASSPHRASE="" pulumi up --yes --stack dev
```

## Access Services

| Service | URL |
|---------|-----|
| Trino UI | http://localhost:8080 |
| MinIO API | http://localhost:9000 |
| MinIO Console | http://localhost:9001 |

### Connect to Trino

Using Docker:

```bash
docker run --rm -it --add-host=host.docker.internal:host-gateway \
  trinodb/trino:latest trino --server http://host.docker.internal:8080
```

Or install the Trino CLI and connect directly:

```bash
trino --server http://localhost:8080
```

### Example Queries

```sql
-- List catalogs
SHOW CATALOGS;

-- Create a schema
CREATE SCHEMA lakehouse.test WITH (location = 's3a://warehouse/iceberg/test');

-- Create an Iceberg table
CREATE TABLE lakehouse.test.test (
    id INT,
    created_at TIMESTAMP
) WITH (format = 'PARQUET');

-- Insert data
INSERT INTO lakehouse.test.test VALUES (1, current_timestamp);

-- Query data
SELECT * FROM lakehouse.test.test;
```

## Cleanup

Destroy all resources:

```bash
PULUMI_CONFIG_PASSPHRASE="" pulumi destroy --yes --stack dev
```

Delete the Kind cluster:

```bash
kind delete cluster --name trino
```

## Troubleshooting

### Check pod status

```bash
kubectl get pods -n trino
```

### View logs

```bash
kubectl logs -n trino deployment/hive-metastore
kubectl logs -n trino deployment/postgres
```

### Restart a deployment

```bash
kubectl rollout restart deployment/hive-metastore -n trino
```

