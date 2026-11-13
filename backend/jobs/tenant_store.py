"""AA-1093 part 3 — where a TripPlanner tenant backup lives: the CIS gold bucket, prefix
``exports/tenants/<tenant>/tripplanner/<backup id>/`` (plan decision D11).

The bucket's ``exports-expire`` lifecycle rule deletes everything under ``exports/`` after 7 days (AA-CIS-Infra
modules/s3), which is the retention of a backup; the bucket encrypts every object at rest (SSE-S3) and every put here
asks for it explicitly. The jobs Lambda's role may put and get objects under ``exports/tenants/*/tripplanner/*`` only
(AA-CIS-Infra ``tripplanner_jobs.tf``), and nothing lists the bucket: a backup is found by its job row, which names
its prefix, and every object of it has a fixed name (``SHA256SUMS``, ``manifest.json``, ``tables/<table>.jsonl``).

The bucket name comes from the Lambda environment (``TENANT_EXPORT_BUCKET``, set by Terraform): this repo is public,
so no account id is written here. Without it a backup or a drill fails at once (a fixed message, no retry).
"""
from __future__ import annotations

import asyncio
import os
from collections.abc import Mapping
from typing import Optional, Protocol

BUCKET_ENV = "TENANT_EXPORT_BUCKET"
SSE = "AES256"
APP = "tripplanner"


class ObjectMissing(Exception):
    """The object is not there (the 7-day rule took it, or it was never written). Without ListBucket S3 answers 403
    for a missing key, so 403 counts as missing too."""


class BucketNotConfigured(Exception):
    """TENANT_EXPORT_BUCKET is not set on this deployment."""


def prefix(tenant_id: str, backup_id: str) -> str:
    return f"exports/tenants/{tenant_id}/{APP}/{backup_id}/"


def bucket_name(environ: Optional[Mapping[str, str]] = None) -> str:
    name = ((environ if environ is not None else os.environ).get(BUCKET_ENV) or "").strip()
    if not name:
        raise BucketNotConfigured(f"{BUCKET_ENV} is not set on this deployment")
    return name


class ObjectStore(Protocol):
    async def put_bytes(self, key: str, data: bytes, content_type: str) -> None: ...

    async def get_bytes(self, key: str) -> bytes: ...


class S3Store:
    """boto3 behind asyncio.to_thread (the handler's event loop must not block on a transfer)."""

    def __init__(self, bucket: Optional[str] = None, client=None):
        self.bucket = bucket or bucket_name()
        if client is None:
            import boto3  # lazy: the offline tests never build an S3 client

            client = boto3.client("s3", region_name=os.environ.get("AWS_REGION", "us-west-1"))
        self.client = client

    async def put_bytes(self, key: str, data: bytes, content_type: str) -> None:
        await asyncio.to_thread(self.client.put_object, Bucket=self.bucket, Key=key, Body=data,
                                ContentType=content_type, ServerSideEncryption=SSE)

    async def get_bytes(self, key: str) -> bytes:
        def _get() -> bytes:
            try:
                return self.client.get_object(Bucket=self.bucket, Key=key)["Body"].read()
            except Exception as e:  # botocore ClientError: 404 / NoSuchKey, or 403 without ListBucket
                code = str(getattr(e, "response", {}).get("Error", {}).get("Code", ""))
                if code in ("404", "403", "NoSuchKey", "NotFound", "AccessDenied"):
                    raise ObjectMissing(key) from None
                raise
        return await asyncio.to_thread(_get)


class DirStore:
    """A local directory as the store (tests, and a look at a downloaded backup on a laptop)."""

    def __init__(self, root: str):
        self.root = root

    def _path(self, key: str) -> str:
        return os.path.join(self.root, *key.split("/"))

    async def put_bytes(self, key: str, data: bytes, content_type: str) -> None:
        dest = self._path(key)
        os.makedirs(os.path.dirname(dest), exist_ok=True)
        with open(dest, "wb") as f:
            f.write(data)

    async def get_bytes(self, key: str) -> bytes:
        src = self._path(key)
        if not os.path.exists(src):
            raise ObjectMissing(key)
        with open(src, "rb") as f:
            return f.read()
