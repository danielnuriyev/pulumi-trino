# Trino on Kubernetes with Pulumi

Deploy Trino to local Kubernetes cluster created with `kind` using pulumi.
The deployment uses Nessie as the Iceberg catalog with Git-like versioning capabilities, storing metadata in Postgres and files on S3 using MinIO.

## Architecture

- **Trino** - Distributed SQL query engine (1 coordinator + 3 workers)
- **Nessie** - Iceberg catalog server with Git-like branching and versioning
- **MinIO** - S3-compatible object storage for Iceberg data
- **PostgreSQL 17** - Metadata storage for Nessie

## Prerequisites

### 1. Install Docker Desktop

Download and install from [docker.com](https://www.docker.com/products/docker-desktop/)

Configure Docker Resources to have 4 CPUs, 8GB of memory, 2GB of swap.

### 2. Install Homebrew (macOS)

```bash
/bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"
echo 'eval "$(/opt/homebrew/bin/brew shellenv)"' >> ~/.zprofile
eval "$(/opt/homebrew/bin/brew shellenv)"
```

### 3. Install Required Tools

```bash
brew install kind kubectl helm pulumi uv
```

### 4. Create the Kind Cluster

This project deploys to the shared `local` kind cluster. Make sure it's running:

```bash
kind get clusters
# Should show 'local' in the list

kubectl cluster-info --context kind-local
# Should show cluster info for the local cluster

kubectl get nodes
# Should show 8 nodes (1 control-plane + 7 workers)
```

If the cluster doesn't exist, create it using the configuration from the parent directory:

```bash
# From the parent directory (where kind-config.yaml exists)
kind create cluster --config kind-config.yaml
```

### 5. Configure Pulumi for Local State

```bash
pulumi login file://~
```

This stores Pulumi state locally instead of in Pulumi Cloud.

Set your Pulumi passphrase as an environment variable (add to your `~/.zshrc` or `~/.bashrc`):

```bash
export PULUMI_CONFIG_PASSPHRASE=""
```

This passphrase encrypts your Pulumi secrets. For local development, an empty passphrase is acceptable.

### 6. Install Python Dependencies

```bash
cd pulumi-trino
uv sync
```

### 7. Initialize Pulumi Stack

If this is a fresh clone, initialize the dev stack:

```bash
pulumi stack init dev
```

### 8. Set Required Secrets

Before deploying, you must set passwords for MinIO and PostgreSQL. These are stored as encrypted secrets in `Pulumi.dev.yaml`.

**Important Requirements:**
- PostgreSQL **does not accept empty passwords** by default (requires `POSTGRES_HOST_AUTH_METHOD=trust`)
- MinIO password **cannot be empty** because Trino's Iceberg connector requires valid S3 credentials
- Use non-empty passwords for reliable deployment

```bash
# Set MinIO root password (REQUIRED - cannot be empty)
pulumi config set --secret minioPassword "minioadmin"

# Set PostgreSQL password (REQUIRED - cannot be empty)
pulumi config set --secret pgPassword "postgres"
```

To verify your secrets are set:

```bash
pulumi config
```

You should see `minioPassword` and `pgPassword` listed as `[secret]` if custom values were set.

**Alternative Configuration:**
The deployment includes PostgreSQL trust authentication to support flexible password policies, but production deployments should use strong passwords.

**Current Working Configuration:**
- MinIO Password: `minioadmin`
- PostgreSQL Password: `postgres`

## Deploy

```bash
pulumi up --yes --stack dev
```

Once deployment completes (typically 2-3 minutes), all services will be running and ready to use.

## Architecture Details

### PostgreSQL Configuration
- Uses trust authentication (`POSTGRES_HOST_AUTH_METHOD=trust`) to allow password-less connections
- Required because the deployment needs to work with various password configurations
- Stores metadata for both Hive Metastore and Nessie

### MinIO Configuration
- S3-compatible object storage for Iceberg data files
- Credentials are shared with Trino for seamless S3 access
- Includes a default bucket named "warehouse"

### Nessie Configuration
- Uses PostgreSQL as backend storage for version metadata
- Provides Git-like branching and versioning for Iceberg tables
- Integrated with Trino as the primary catalog

### Trino Configuration
- Iceberg catalog type: `nessie`
- S3 endpoint: Internal MinIO service
- Coordinator + 3 workers for distributed processing

## Access Services

**Direct NodePort Access** (when Kind cluster port mappings work):
| Service | URL | Status |
|---------|-----|--------|
| Trino UI | http://localhost:8080 |  |
| Nessie UI | http://localhost:19120 |  |
| MinIO API | http://localhost:9000 |  |
| MinIO Console | http://localhost:9001 |  |

**Port Forwarding** (recommended - always works): See below for commands.

**⚠️ Port Conflicts:** Docker Desktop uses both ports 9000 (Container Station) and 9001 (ETL Service Manager), blocking direct access to MinIO's NodePort mappings.

For more stable and reliable access, especially to Nessie which doesn't expose a NodePort by default:

**Nessie UI:**
```bash
kubectl port-forward -n trino svc/nessie 19120:19120 --context kind-local
```

Then open http://localhost:19120 in your browser.

#### Port Forward to MinIO API

```bash
# Find the correct MinIO API service name (usually minio-<random-id>)
kubectl get services -n trino --context kind-local | grep minio

# Port-forward the API service (example with actual service name)
kubectl port-forward -n trino svc/minio-adf72f43 9000:9000 --context kind-local
```

#### Port Forward to MinIO Console

```bash
# Port-forward the console service (example with actual service name)
kubectl port-forward -n trino svc/minio-adf72f43-console 9001:9001 --context kind-local

# Alternative: Use a different local port if 9001 conflicts
kubectl port-forward -n trino svc/minio-adf72f43-console 9091:9001 --context kind-local
# Then access http://localhost:9091
```

**Trino UI:**
```bash
# Find the Trino service name
kubectl get svc -n trino --context kind-local | grep "trino.*NodePort"

# Port-forward to it (example service name)
kubectl port-forward -n trino svc/trino-xxxxx-trino 8080:8080 --context kind-local
```

Then open http://localhost:8080 in your browser and use `admin` as the password.

**MinIO Console:**
```bash
# Find the MinIO console service name
kubectl get svc -n trino --context kind-local | grep "minio.*console"

# Port-forward to it (example service name)
kubectl port-forward -n trino svc/minio-xxxxx-console 9001:9001 --context kind-local
```

Then open http://localhost:9001 in your browser. Login with `admin` / `minioadmin`.

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

-- List available Nessie branches
SELECT * FROM lakehouse.information_schema.nessie_branches;

-- Create a development branch (requires Nessie CLI or REST API)
-- Use: nessie branch create dev

-- Switch to a branch (use Nessie CLI: nessie branch use dev)
-- Or set branch in session: SET SESSION iceberg.nessie.branch = 'dev';

-- Create a schema on the main branch
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

-- Show table history (Nessie versioning)
SELECT * FROM lakehouse.information_schema.nessie_table_history
WHERE namespace = 'test' AND table_name = 'test';
```

### Working with Nessie Branches

Nessie provides Git-like branching and versioning for your Iceberg tables. To use advanced branching features, install the Nessie CLI:

```bash
# Download and install Nessie CLI
curl -L https://github.com/projectnessie/nessie/releases/latest/download/nessie-cli.jar -o nessie-cli.jar
java -jar nessie-cli.jar --help
```

Connect to your Nessie instance:

```bash
# Set the Nessie endpoint
export NESSIE_URI=http://localhost:19120/api/v1

# List branches
nessie branch

# Create a new branch
nessie branch create feature/new-table

# Switch to a branch
nessie branch use feature/new-table

# Create tables on specific branches
nessie branch use feature/new-table
# Then run your CREATE TABLE statements in Trino

# Merge branches (requires setting up merge rules)
nessie merge main
```

## Cleanup

Destroy all resources:

```bash
pulumi destroy --yes --stack dev
```

Delete the trino namespace:

```bash
kubectl delete namespace trino --context kind-local
```

**Note**: The local kind cluster is shared with other services and should not be deleted.

## Troubleshooting

### Deployment Issues

#### PostgreSQL fails to start with "Database is uninitialized and superuser password is not specified"
**Problem:** PostgreSQL refuses empty passwords by default.
**Solution:** Set a non-empty PostgreSQL password:
```bash
pulumi config set --secret pgPassword "postgres"
pulumi up --yes --stack dev
```

#### Trino pods crash with "Secret access key cannot be blank"
**Problem:** MinIO password is empty, but Trino's Iceberg connector requires valid S3 credentials.
**Solution:** Set a non-empty MinIO password:
```bash
pulumi config set --secret minioPassword "minioadmin"
pulumi up --yes --stack dev
```

#### Cannot access MinIO API on localhost:9000
**Problem:** Docker Desktop uses port 9000 for its Container Station service.
**Solution:** Use port forwarding instead:
```bash
kubectl port-forward -n trino svc/minio-adf72f43 9000:9000 --context kind-local
# Then access http://localhost:9000
```

#### Cannot access MinIO Console on localhost:9001
**Problem:** Docker Desktop uses port 9001 for its ETL Service Manager.
**Solution:** Use port forwarding instead:
```bash
kubectl port-forward -n trino svc/minio-adf72f43-console 9001:9001 --context kind-local
# Then access http://localhost:9001
# Username: admin, Password: minioadmin
```

#### Pulumi reports "resource not found" or update conflicts
**Problem:** Stack state is out of sync from previous failed deployments.
**Solution:** Reset the stack:
```bash
pulumi stack rm dev --force --yes
pulumi stack init dev
# Re-set your secrets
pulumi up --yes --stack dev
```

#### Helm timeout during Trino deployment
**Problem:** Trino Helm release times out during initial deployment.
**Solution:** The deployment may still succeed. Check pod status:
```bash
kubectl get pods -n trino --context kind-local
```

### Runtime Issues

### Check pod status

```bash
kubectl get pods -n trino --context kind-local
```

### View logs

```bash
kubectl logs -n trino deployment/nessie --context kind-local
kubectl logs -n trino deployment/postgres --context kind-local
kubectl logs -n trino deployment/hive-metastore --context kind-local
kubectl logs -n trino deployment/trino-coordinator --context kind-local
```

### Check init container status

To debug init containers that are still initializing:

```bash
# Describe a specific pod to see init container status
kubectl describe pod -n trino <pod-name> --context kind-local

# View init container logs
kubectl logs -n trino <pod-name> -c <init-container-name> --context kind-local
```

### Restart a deployment

```bash
kubectl rollout restart deployment/nessie -n trino --context kind-local
kubectl rollout restart deployment/hive-metastore -n trino --context kind-local
kubectl rollout restart deployment/postgres -n trino --context kind-local
```

### Trino pods crashing or in CrashLoopBackOff

Trino requires significant memory. If Trino pods are crashing with `OOMKilled` or `CrashLoopBackOff`:

1. Check available node resources:
   ```bash
   kubectl describe nodes --context kind-local
   ```

2. Check pod resource usage:
   ```bash
   kubectl top pods -n trino --context kind-local
   ```

3. If memory is constrained, you may need to:
   - Reduce the number of Trino workers in the `__main__.py` configuration
   - Allocate more resources to the Kind cluster
   - Run on a machine with more available memory

### PostgreSQL connection issues

If Nessie or Hive Metastore pods fail to initialize with "Waiting for postgres" messages:

1. Verify PostgreSQL is running:
   ```bash
   kubectl logs -n trino deployment/postgres --context kind-local
   ```

2. Test connection from another pod:
   ```bash
   kubectl run -it --rm debug --image=bitnami/postgresql:latest --restart=Never -n trino --context kind-local -- /opt/bitnami/postgresql/bin/pg_isready -h postgres -p 5432
   ```

3. Check PostgreSQL service:
   ```bash
   kubectl get svc -n trino postgres --context kind-local
   kubectl describe svc -n trino postgres --context kind-local
   ```

## Deployment Summary

This deployment creates a fully functional Trino data lake environment on Kubernetes with:

✅ **PostgreSQL** - Metadata storage for Nessie and Hive Metastore  
✅ **MinIO** - S3-compatible object storage with Iceberg support  
✅ **Nessie** - Iceberg catalog server with Git-like versioning  
✅ **Hive Metastore** - Additional metadata store for interoperability  
✅ **Trino** - Distributed SQL query engine with 1 coordinator + 3 workers  

### Default Credentials

- **MinIO Console**: Username `admin` / Password `minioadmin`
- **Trino**: No authentication (open access)
- **Nessie**: No authentication (open access)
- **PostgreSQL**: Username `metastore` / Password `metastore`

### Known Issues

- If Trino pods are crashing immediately after deployment, check the "Trino pods crashing" section in Troubleshooting above
- The first deployment may take 2-3 minutes for all components to start up
- Nessie requires TCP connectivity check during initialization; ensure networking is stable

### Next Steps

1. Access the Trino UI at http://localhost:8080
2. Create schemas and tables using the Lakehouse catalog
3. Use the Nessie CLI for advanced branching and versioning
4. Monitor performance using kubectl logs and pod metrics

For detailed Trino documentation, visit: https://trino.io/docs/
