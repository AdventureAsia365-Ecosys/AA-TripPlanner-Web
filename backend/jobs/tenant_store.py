"""AA-1093 part 3 — where a TripPlanner tenant backup lives: the CIS gold bucket, prefix
``exports/tenants/<tenant>/tripplanner/<backup id>/`` (plan decision D11).

The bucket's ``exports-expire`` lifecycle rule deletes everything under ``exports/`` after 7 days (AA-CIS-Infra
modules/s3), which is the retention of a backup; the bucket encrypts every object at rest (SSE-S3) and every put here
asks for it explicitly. The jobs Lambda's role may put and get objects under ``exports/tenants/*/tripplanner/*`` only
(AA-CIS-Infra ``tripplanner_jobs.tf``), and nothing lists the bucket: a backup is found by its job row, which names
its prefix, and every object of it has a fixed name (``SHA256SUMS``, ``manifest.json``, ``tables/<table>.jsonl``).

The bucket name comes from the Lambda environment (``TENANT_EXPORT_BUCKET``, set by Terraform): this repo is public,
so no account id is written here. Without it a backup or a drill fails at once (a fixed message, no retry).

Errors: NoSuchKey / 404 is :class:`ObjectMissing`; 403 / AccessDenied is :class:`AccessDenied`, which the backup and
the drill turn into a failed job whose error starts with ``s3_access_denied`` (:func:`denied`), never into "missing".
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
    """The object is not there (the 7-day rule took it, or it was never written): S3 answered NoSuchKey / 404."""


class AccessDenied(Exception):
    """S3 refused the call (403 / AccessDenied): the role lacks the grant on that prefix, or the bucket policy / key
    refuses it. Never read as "missing": a wrong grant must fail the job loudly (reason ``s3_access_denied``), not
    pass as a backup the 7-day rule already took. Note: S3 also answers 403 for a missing key when the caller has no
    ListBucket, which this role never has (nothing lists the bucket), so a drill of an expired backup fails with this
    reason too; the job row of the backup (its date) tells the two apart."""


# The job's failure reason for an AccessDenied (runner: a PermanentError fails the job at once with its message).
ACCESS_DENIED_REASON = "s3_access_denied"
MISSING_CODES = ("404", "NoSuchKey", "NotFound")
DENIED_CODES = ("403", "AccessDenied", "AllAccessDisabled")


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
        def _put() -> None:
            try:
                self.client.put_object(Bucket=self.bucket, Key=key, Body=data, ContentType=content_type,
                                       ServerSideEncryption=SSE)
            except Exception as e:  # botocore ClientError
                if error_code(e) in DENIED_CODES:
                    raise AccessDenied(key) from None
                raise
        await asyncio.to_thread(_put)

    async def get_bytes(self, key: str) -> bytes:
        def _get() -> bytes:
            try:
                return self.client.get_object(Bucket=self.bucket, Key=key)["Body"].read()
            except Exception as e:  # botocore ClientError: NoSuchKey / 404 = missing; 403 / AccessDenied = denied
                code = error_code(e)
                if code in MISSING_CODES:
                    raise ObjectMissing(key) from None
                if code in DENIED_CODES:
                    raise AccessDenied(key) from None
                raise
        return await asyncio.to_thread(_get)


def error_code(exc: BaseException) -> str:
    """The S3 error code of a botocore ClientError (``response["Error"]["Code"]``), or "" for anything else."""
    response = getattr(exc, "response", None)
    if not isinstance(response, dict):
        return ""
    return str((response.get("Error") or {}).get("Code") or "")


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


def denied(exc: AccessDenied) -> str:
    """The fixed failure text of a job S3 refused (the reason code first; no bucket name, no tenant id)."""
    return f"{ACCESS_DENIED_REASON}: S3 refused a call on the tenant backup prefix (role grant or bucket policy)"
