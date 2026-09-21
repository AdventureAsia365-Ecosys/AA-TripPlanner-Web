"""Bedrock access — hybrid, verified live against the real accounts.

Two paths, because acc2 (005097885195, where the Lambdas run) is an AWS
channel-program account that CANNOT invoke Anthropic Claude:

  compose / renarrate (Claude)  -> SATELLITE: assume a cross-account role
      (acc3 786888028788 primary, acc1 867490540162 fallback) that is
      trusted to call Bedrock, then invoke with those temp creds. Each
      account uses its own ExternalId (config). Trust is added via
      Terraform in AA-CIS-Infra (human-applied); this module only consumes.

  embed (Cohere Embed v4)       -> DIRECT: acc2 CAN invoke Cohere
      embeddings, so no assume-role — the Lambda's own execution role is
      granted bedrock:InvokeModel on the Cohere inference profile.

All models are addressed by inference-profile id (e.g.
us.anthropic.claude-sonnet-4-6, us.cohere.embed-v4:0); bare foundation-
model ids fail with ValidationException.

Testability: all AWS access goes through an injectable client factory
(`ClientFactory`); unit tests pass a stub so no live AWS call is made and
boto3 need not be installed. Nothing reads AWS keys — creds come from the
Lambda role's ambient chain (IAM role only).
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Callable, Iterator, Optional, Protocol

from backend import config


class BedrockError(RuntimeError):
    """Raised when both primary and fallback accounts fail."""


class _StsClient(Protocol):
    def assume_role(self, **kwargs: Any) -> dict: ...


class _BedrockClient(Protocol):
    def invoke_model(self, *, modelId: str, body: str, **kwargs: Any) -> dict: ...
    def invoke_model_with_response_stream(
        self, *, modelId: str, body: str, **kwargs: Any
    ) -> dict: ...


@dataclass
class Credentials:
    access_key_id: str
    secret_access_key: str
    session_token: str

    @classmethod
    def from_sts_response(cls, resp: dict) -> "Credentials":
        c = resp["Credentials"]
        return cls(
            access_key_id=c["AccessKeyId"],
            secret_access_key=c["SecretAccessKey"],
            session_token=c["SessionToken"],
        )


# A factory that, given a service name and (optional) credentials, returns
# a client. In production this wraps boto3.client; in tests it's a stub.
ClientFactory = Callable[..., Any]


def _default_client_factory(service: str, *, creds: Optional[Credentials] = None,
                            region: Optional[str] = None) -> Any:
    import boto3  # imported lazily so tests never need boto3 installed

    kwargs: dict[str, Any] = {"region_name": region or config.BEDROCK_REGION}
    if creds is not None:
        kwargs.update(
            aws_access_key_id=creds.access_key_id,
            aws_secret_access_key=creds.secret_access_key,
            aws_session_token=creds.session_token,
        )
    return boto3.client(service, **kwargs)


def _role_arn(account_id: str, role_name: str) -> str:
    role = role_name or config.BEDROCK_ROLE_NAME
    if not role:
        raise BedrockError(
            "No satellite role name configured for account "
            f"{account_id} — cannot assume a cross-account Bedrock role."
        )
    return f"arn:aws:iam::{account_id}:role/{role}"


def _assume(
    account_id: str, role_name: str, external_id: str, client_factory: ClientFactory
) -> Credentials:
    sts: _StsClient = client_factory("sts")
    kwargs: dict[str, Any] = {
        "RoleArn": _role_arn(account_id, role_name),
        "RoleSessionName": "tripplanner-bedrock",
    }
    if external_id:
        kwargs["ExternalId"] = external_id
    resp = sts.assume_role(**kwargs)
    return Credentials.from_sts_response(resp)


def _bedrock_for_account(
    account_id: str, role_name: str, external_id: str, client_factory: ClientFactory
) -> _BedrockClient:
    creds = _assume(account_id, role_name, external_id, client_factory)
    return client_factory("bedrock-runtime", creds=creds, region=config.BEDROCK_REGION)


def _with_failover(
    op: Callable[[_BedrockClient], Any],
    client_factory: ClientFactory,
) -> Any:
    """Run `op` against acc3 (primary); on failure, retry against acc1
    (fallback). Each account has its own ExternalId (see config)."""
    attempts = [
        (
            config.BEDROCK_ACCT_PRIMARY,
            config.BEDROCK_ROLE_NAME_PRIMARY,
            config.BEDROCK_EXTERNAL_ID_PRIMARY,
        ),
        (
            config.BEDROCK_ACCT_FALLBACK,
            config.BEDROCK_ROLE_NAME_FALLBACK,
            config.BEDROCK_EXTERNAL_ID_FALLBACK,
        ),
    ]
    last_err: Optional[Exception] = None
    for acct, role_name, ext_id in attempts:
        try:
            client = _bedrock_for_account(acct, role_name, ext_id, client_factory)
            return op(client)
        except Exception as e:  # noqa: BLE001 — deliberately broad, we failover
            last_err = e
            continue
    raise BedrockError(
        f"Bedrock invoke failed on both {attempts[0][0]} and "
        f"{attempts[1][0]}: {last_err}"
    ) from last_err


def invoke(
    model_id: str,
    body: dict,
    *,
    client_factory: ClientFactory = _default_client_factory,
) -> dict:
    """Invoke a Bedrock model and return the parsed JSON response body."""

    def op(client: _BedrockClient) -> dict:
        resp = client.invoke_model(modelId=model_id, body=json.dumps(body))
        raw = resp["body"].read() if hasattr(resp["body"], "read") else resp["body"]
        return json.loads(raw)

    return _with_failover(op, client_factory)


def invoke_stream(
    model_id: str,
    body: dict,
    *,
    client_factory: ClientFactory = _default_client_factory,
) -> Iterator[dict]:
    """Invoke with response streaming; yields parsed chunk dicts.

    Materializes the stream inside the failover boundary so that a failure
    to establish the stream triggers fallback, but iteration itself is
    lazy to the caller.
    """

    def op(client: _BedrockClient) -> list[dict]:
        resp = client.invoke_model_with_response_stream(
            modelId=model_id, body=json.dumps(body)
        )
        chunks: list[dict] = []
        for event in resp["body"]:
            payload = event.get("chunk", {}).get("bytes")
            if payload is None:
                continue
            chunks.append(json.loads(payload))
        return chunks

    for chunk in _with_failover(op, client_factory):
        yield chunk


def _extract_cohere_embedding(resp: dict) -> list[float]:
    """Pull the first embedding vector out of a Cohere Embed v4 response.

    Response shape (verified live): {"embeddings": {"float": [[...]]}, ...}
    Older/other shapes use {"embeddings": [[...]]}; handle both.
    """
    emb = resp.get("embeddings")
    if isinstance(emb, dict):
        emb = emb.get("float") or emb.get("embeddings")
    if isinstance(emb, list) and emb and isinstance(emb[0], list):
        return [float(x) for x in emb[0]]
    raise BedrockError(f"Unexpected embedding response shape: {list(resp)[:6]}")


def embed(
    text: str,
    *,
    model_id: Optional[str] = None,
    client_factory: ClientFactory = _default_client_factory,
    input_type: str = "search_document",
) -> list[float]:
    """Return an embedding vector for `text` via a DIRECT acc2 Bedrock call.

    Unlike compose/renarrate (Claude, satellite), embeddings run on acc2
    itself — acc2 can invoke Cohere Embed v4 even though it can't invoke
    Claude. No assume-role here: the Lambda's own execution role is granted
    bedrock:InvokeModel on the Cohere inference profile.
    """
    model = model_id or config.BEDROCK_MODEL_EMBED
    body = {"texts": [text], "input_type": input_type}
    client = client_factory("bedrock-runtime", region=config.BEDROCK_REGION)
    resp_raw = client.invoke_model(modelId=model, body=json.dumps(body))
    raw = resp_raw["body"].read() if hasattr(resp_raw["body"], "read") else resp_raw["body"]
    resp = json.loads(raw)
    vec = _extract_cohere_embedding(resp)
    if len(vec) != config.EMBED_DIM:
        raise BedrockError(
            f"Embedding dim {len(vec)} != expected {config.EMBED_DIM}"
        )
    return vec
