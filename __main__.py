import pulumi
import pulumi_kubernetes as k8s

cfg = pulumi.Config()

ns_name = cfg.get("namespace") or "trino"

minio_user = cfg.get("minioUser") or "admin"
minio_pass = cfg.require_secret("minioPassword")

pg_user = cfg.get("pgUser") or "metastore"
pg_pass = cfg.require_secret("pgPassword")
pg_db   = cfg.get("pgDb") or "metastore"

ns = k8s.core.v1.Namespace("ns", metadata={"name": ns_name})

# --- Standalone PostgreSQL with md5 auth for Hive Metastore ---
pg_deploy = k8s.apps.v1.Deployment(
    "postgres",
    metadata=k8s.meta.v1.ObjectMetaArgs(
        name="postgres",
        namespace=ns.metadata["name"],
    ),
    spec=k8s.apps.v1.DeploymentSpecArgs(
        replicas=1,
        selector=k8s.meta.v1.LabelSelectorArgs(
            match_labels={"app": "postgres"},
        ),
        template=k8s.core.v1.PodTemplateSpecArgs(
            metadata=k8s.meta.v1.ObjectMetaArgs(labels={"app": "postgres"}),
            spec=k8s.core.v1.PodSpecArgs(
                containers=[
                    k8s.core.v1.ContainerArgs(
                        name="postgres",
                        image="postgres:17-alpine",
                        ports=[k8s.core.v1.ContainerPortArgs(container_port=5432)],
                        env=[
                            k8s.core.v1.EnvVarArgs(name="POSTGRES_USER", value=pg_user),
                            k8s.core.v1.EnvVarArgs(name="POSTGRES_PASSWORD", value=pg_pass),
                            k8s.core.v1.EnvVarArgs(name="POSTGRES_DB", value=pg_db),
                        ],
                    )
                ],
            ),
        ),
    ),
    opts=pulumi.ResourceOptions(depends_on=[ns]),
)

pg_svc = k8s.core.v1.Service(
    "postgres-svc",
    metadata=k8s.meta.v1.ObjectMetaArgs(
        name="postgres",
        namespace=ns.metadata["name"],
    ),
    spec=k8s.core.v1.ServiceSpecArgs(
        selector={"app": "postgres"},
        ports=[k8s.core.v1.ServicePortArgs(port=5432, target_port=5432)],
    ),
    opts=pulumi.ResourceOptions(depends_on=[pg_deploy]),
)

# --- MinIO (Official) ---
minio = k8s.helm.v3.Release(
    "minio",
    k8s.helm.v3.ReleaseArgs(
        chart="minio",
        namespace=ns.metadata["name"],
        repository_opts=k8s.helm.v3.RepositoryOptsArgs(
            repo="https://charts.min.io/",
        ),
        values={
            "rootUser": minio_user,
            "rootPassword": minio_pass,
            "replicas": 1,
            "mode": "standalone",
            "resources": {
                "requests": {"memory": "256Mi", "cpu": "1"},
            },
            "buckets": [{"name": "warehouse", "policy": "none", "purge": False}],
            "service": {"type": "NodePort", "nodePort": 30900},
            "consoleService": {"type": "NodePort", "nodePort": 30901},
            "persistence": {"enabled": False},
        },
    ),
    opts=pulumi.ResourceOptions(depends_on=[ns]),
)

# --- Hive Metastore with S3 support ---
# Using official Apache Hive 4.0.1 image in standalone metastore mode

# Kubernetes Secret for sensitive credentials
hms_secret = k8s.core.v1.Secret(
    "hms-secret",
    metadata=k8s.meta.v1.ObjectMetaArgs(
        name="hive-metastore-secret",
        namespace=ns.metadata["name"],
    ),
    string_data={
        "pg-password": pg_pass,
        "minio-access-key": minio_user,
        "minio-secret-key": minio_pass,
    },
    opts=pulumi.ResourceOptions(depends_on=[ns]),
)

# ConfigMap with non-sensitive config using env var substitution
hms_config = k8s.core.v1.ConfigMap(
    "hms-config",
    metadata=k8s.meta.v1.ObjectMetaArgs(
        name="hive-metastore-config",
        namespace=ns.metadata["name"],
    ),
    data={
        # Hadoop/Hive support ${env.VAR_NAME} for environment variable substitution
        "hive-site.xml": minio.name.apply(
            lambda minio_name: f"""<?xml version="1.0"?>
<configuration>
    <!-- PostgreSQL connection -->
    <property>
        <name>javax.jdo.option.ConnectionDriverName</name>
        <value>org.postgresql.Driver</value>
    </property>
    <property>
        <name>javax.jdo.option.ConnectionURL</name>
        <value>jdbc:postgresql://postgres:5432/{pg_db}</value>
    </property>
    <property>
        <name>javax.jdo.option.ConnectionUserName</name>
        <value>{pg_user}</value>
    </property>
    <property>
        <name>javax.jdo.option.ConnectionPassword</name>
        <value>${{env.PG_PASSWORD}}</value>
    </property>
    <!-- Warehouse location -->
    <property>
        <name>hive.metastore.warehouse.dir</name>
        <value>s3a://warehouse/iceberg/</value>
    </property>
    <!-- S3A configuration for MinIO -->
    <property>
        <name>fs.s3a.endpoint</name>
        <value>http://{minio_name}:9000</value>
    </property>
    <property>
        <name>fs.s3a.access.key</name>
        <value>${{env.AWS_ACCESS_KEY_ID}}</value>
    </property>
    <property>
        <name>fs.s3a.secret.key</name>
        <value>${{env.AWS_SECRET_ACCESS_KEY}}</value>
    </property>
    <property>
        <name>fs.s3a.path.style.access</name>
        <value>true</value>
    </property>
    <property>
        <name>fs.s3a.impl</name>
        <value>org.apache.hadoop.fs.s3a.S3AFileSystem</value>
    </property>
    <property>
        <name>fs.s3a.connection.ssl.enabled</name>
        <value>false</value>
    </property>
</configuration>
"""
        ),
        "core-site.xml": minio.name.apply(
            lambda minio_name: f"""<?xml version="1.0"?>
<configuration>
    <property>
        <name>fs.defaultFS</name>
        <value>s3a://warehouse</value>
    </property>
    <property>
        <name>fs.s3a.endpoint</name>
        <value>http://{minio_name}:9000</value>
    </property>
    <property>
        <name>fs.s3a.access.key</name>
        <value>${{env.AWS_ACCESS_KEY_ID}}</value>
    </property>
    <property>
        <name>fs.s3a.secret.key</name>
        <value>${{env.AWS_SECRET_ACCESS_KEY}}</value>
    </property>
    <property>
        <name>fs.s3a.path.style.access</name>
        <value>true</value>
    </property>
    <property>
        <name>fs.s3a.impl</name>
        <value>org.apache.hadoop.fs.s3a.S3AFileSystem</value>
    </property>
    <property>
        <name>fs.s3a.connection.ssl.enabled</name>
        <value>false</value>
    </property>
</configuration>
"""
        ),
    },
    opts=pulumi.ResourceOptions(depends_on=[ns, minio]),
)

hms_deploy = k8s.apps.v1.Deployment(
    "hive-metastore",
    metadata=k8s.meta.v1.ObjectMetaArgs(
        name="hive-metastore",
        namespace=ns.metadata["name"],
    ),
    spec=k8s.apps.v1.DeploymentSpecArgs(
        replicas=1,
        selector=k8s.meta.v1.LabelSelectorArgs(
            match_labels={"app": "hive-metastore"},
        ),
        template=k8s.core.v1.PodTemplateSpecArgs(
            metadata=k8s.meta.v1.ObjectMetaArgs(labels={"app": "hive-metastore"}),
            spec=k8s.core.v1.PodSpecArgs(
                init_containers=[
                    # Download PostgreSQL JDBC driver
                    k8s.core.v1.ContainerArgs(
                        name="download-postgres-driver",
                        image="curlimages/curl:8.11.1",
                        command=[
                            "sh", "-c",
                            "curl -fsSL -o /jdbc/postgresql-42.7.4.jar "
                            "https://jdbc.postgresql.org/download/postgresql-42.7.4.jar",
                        ],
                        volume_mounts=[
                            k8s.core.v1.VolumeMountArgs(
                                name="jdbc-drivers",
                                mount_path="/jdbc",
                            ),
                        ],
                    ),
                    # Wait for PostgreSQL to be ready
                    k8s.core.v1.ContainerArgs(
                        name="wait-for-postgres",
                        image="busybox:1.37",
                        command=["sh", "-c", "until nc -z postgres 5432; do echo waiting for postgres; sleep 2; done;"],
                    ),
                ],
                containers=[
                    k8s.core.v1.ContainerArgs(
                        name="metastore",
                        image="apache/hive:4.0.1",
                        ports=[k8s.core.v1.ContainerPortArgs(container_port=9083)],
                        env=[
                            k8s.core.v1.EnvVarArgs(name="SERVICE_NAME", value="metastore"),
                            k8s.core.v1.EnvVarArgs(name="DB_DRIVER", value="postgres"),
                            k8s.core.v1.EnvVarArgs(name="HIVE_CUSTOM_CONF_DIR", value="/opt/hive/custom-conf"),
                            # Add AWS jars and JDBC driver to classpath
                            k8s.core.v1.EnvVarArgs(
                                name="HADOOP_CLASSPATH",
                                value="/opt/hadoop/share/hadoop/tools/lib/*:/jdbc/*",
                            ),
                            # Inject secrets as environment variables
                            k8s.core.v1.EnvVarArgs(
                                name="PG_PASSWORD",
                                value_from=k8s.core.v1.EnvVarSourceArgs(
                                    secret_key_ref=k8s.core.v1.SecretKeySelectorArgs(
                                        name="hive-metastore-secret",
                                        key="pg-password",
                                    ),
                                ),
                            ),
                            k8s.core.v1.EnvVarArgs(
                                name="AWS_ACCESS_KEY_ID",
                                value_from=k8s.core.v1.EnvVarSourceArgs(
                                    secret_key_ref=k8s.core.v1.SecretKeySelectorArgs(
                                        name="hive-metastore-secret",
                                        key="minio-access-key",
                                    ),
                                ),
                            ),
                            k8s.core.v1.EnvVarArgs(
                                name="AWS_SECRET_ACCESS_KEY",
                                value_from=k8s.core.v1.EnvVarSourceArgs(
                                    secret_key_ref=k8s.core.v1.SecretKeySelectorArgs(
                                        name="hive-metastore-secret",
                                        key="minio-secret-key",
                                    ),
                                ),
                            ),
                        ],
                        volume_mounts=[
                            k8s.core.v1.VolumeMountArgs(
                                name="config",
                                mount_path="/opt/hive/custom-conf",
                            ),
                            k8s.core.v1.VolumeMountArgs(
                                name="jdbc-drivers",
                                mount_path="/jdbc",
                            ),
                        ],
                    )
                ],
                volumes=[
                    k8s.core.v1.VolumeArgs(
                        name="config",
                        config_map=k8s.core.v1.ConfigMapVolumeSourceArgs(
                            name="hive-metastore-config",
                        ),
                    ),
                    k8s.core.v1.VolumeArgs(
                        name="jdbc-drivers",
                        empty_dir=k8s.core.v1.EmptyDirVolumeSourceArgs(),
                    ),
                ],
            ),
        ),
    ),
    opts=pulumi.ResourceOptions(depends_on=[pg_svc, minio, hms_config, hms_secret]),
)

hms_svc = k8s.core.v1.Service(
    "hive-metastore-svc",
    metadata=k8s.meta.v1.ObjectMetaArgs(
        name="hive-metastore",
        namespace=ns.metadata["name"],
    ),
    spec=k8s.core.v1.ServiceSpecArgs(
        selector={"app": "hive-metastore"},
        ports=[k8s.core.v1.ServicePortArgs(port=9083, target_port=9083)],
    ),
    opts=pulumi.ResourceOptions(depends_on=[hms_deploy]),
)

# --- Trino ---
iceberg_catalog = pulumi.Output.all(minio_user, minio_pass, minio.name).apply(
    lambda args: f"""\
connector.name=iceberg
iceberg.catalog.type=hive_metastore
hive.metastore.uri=thrift://hive-metastore:9083

fs.native-s3.enabled=true
s3.endpoint=http://{args[2]}:9000
s3.region=us-east-1
s3.path-style-access=true
s3.aws-access-key={args[0]}
s3.aws-secret-key={args[1]}
"""
)

trino = k8s.helm.v3.Release(
    "trino",
    k8s.helm.v3.ReleaseArgs(
        chart="trino",
        namespace=ns.metadata["name"],
        repository_opts=k8s.helm.v3.RepositoryOptsArgs(
            repo="https://trinodb.github.io/charts",
        ),
        timeout=900,
        values={
            "server": {"workers": 3},
            "service": {"type": "NodePort", "nodePort": 30080},
            "catalogs": {
                "lakehouse": iceberg_catalog,
            },
        },
    ),
    opts=pulumi.ResourceOptions(depends_on=[minio, hms_svc]),
)

pulumi.export("trino_url", "http://localhost:30080")
pulumi.export("minio_api", "http://localhost:30900")
pulumi.export("minio_console", "http://localhost:30901")
