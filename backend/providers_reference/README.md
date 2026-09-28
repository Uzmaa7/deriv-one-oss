# Cloud provider reference (adopter-owned)

The public repository ships **interfaces** in `backend/providers/` and **local
reference implementations** (filesystem, SQLite, in-process queue). It does
**not** ship GCP, AWS, or Azure provider packages — adopters implement those in
their own repos and point environment variables at them.

## Environment variables

| Variable | Interface | Local default |
|----------|-----------|---------------|
| `BLOB_STORE` | `BlobStore` | `local` → `LocalBlobStore` |
| `SECRET_BACKEND` | `SecretBackend` | `local` → `LocalSecretBackend` |
| `MESSAGE_QUEUE` | `MessageQueue` | `inprocess` → `InProcessQueue` |
| `DIRECTORY_PROVIDER` | `DirectoryProvider` | `claims` / `file` / `null` |
| `RETRIEVAL_PROVIDER` | `RetrievalProvider` | `local` |
| `SANDBOX_PROVIDER` | `SandboxProvider` | `container` or `subprocess` |
| `ALERT_SINK` | `AlertSink` | `log` / `webhook` |

Point any variable at a dotted import path:

```bash
BLOB_STORE=acme.platform.gcp:GcsBlobStore
SECRET_BACKEND=acme.platform.gcp:GcpSecretBackend
MESSAGE_QUEUE=acme.platform.gcp:PubSubQueue
```

## What to implement

Read the abstract base classes in:

- `backend/providers/blobs.py`
- `backend/providers/secrets.py`
- `backend/providers/queue.py`
- `backend/providers/directory.py`
- `backend/providers/retrieval.py`
- `backend/providers/sandbox.py`
- `backend/providers/alerts.py`

`backend/providers/__init__.py` documents selection and caching.

## GCP sketch

```python
# acme/platform/gcp/blobs.py — in your private package, not this repo
from google.cloud import storage
from providers.blobs import BlobStore

class GcsBlobStore(BlobStore):
    def __init__(self, bucket: str, **kwargs):
        self._client = storage.Client()
        self._bucket = bucket
    # implement put/get/delete/list/exists ...
```

Dependencies you may use: `requirements-gcp.txt` (SDK pins only — no implementation).

Typical mapping:

| Concern | GCP service |
|---------|-------------|
| Database | Cloud SQL (Postgres) via `DATABASE_URL` |
| Blobs | GCS bucket |
| Secrets | Secret Manager |
| Queue | Pub/Sub |
| Cache | Memorystore Redis (`REDIS_HOST`) |

## AWS sketch

```python
# acme/platform/aws/secrets.py
import boto3
from providers.secrets import SecretBackend, SecretBackendError

class AwsSecretBackend(SecretBackend):
    def __init__(self, prefix: str = "platform/", **kwargs):
        self._client = boto3.client("secretsmanager")
        self._prefix = prefix
    # implement store/resolve/delete ...
```

Typical mapping:

| Concern | AWS service |
|---------|-------------|
| Database | RDS Postgres via `DATABASE_URL` |
| Blobs | S3 |
| Secrets | Secrets Manager |
| Queue | SQS |
| Cache | ElastiCache Redis |

## Kubernetes

Reference manifests live in `deploy/k8s/`. Wire the same ConfigMap/Secret env on
API and worker pods. For sandbox execution on-cluster, implement a
`SandboxProvider` that launches **Jobs** or gVisor/Kata pods instead of using a
host Docker socket.

## Related docs

- [docs/cloud-deployment.md](../../docs/cloud-deployment.md)
- [docs/ADOPTER_GUIDE.md](../../docs/ADOPTER_GUIDE.md)
