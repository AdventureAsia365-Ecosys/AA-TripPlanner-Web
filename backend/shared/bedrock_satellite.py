"""Bedrock transport — cross-account access for the satellite accounts.

acc2 (005097885195, where the Lambdas run) is an AWS channel-program account that CANNOT invoke
Anthropic Claude, so Claude runs on a satellite account: assume a cross-account role (acc3
786888028788 primary, acc1 867490540162 fallback; each with its own ExternalId, see config),
then call Bedrock with those temp creds. The trust is added via Terraform in AA-CIS-Infra
(human-applied); this module only consumes it. acc2 itself can invoke Cohere embeddings.

AA-685: model choice, failover order, pricing and cost logging live in `llm_gateway` (driven by
the shared gateway tables). This module only builds clients.

Testability: all AWS access goes through an injectable client factory (`ClientFactory`); unit
tests pass a stub so no live AWS call is made and boto3 need not be installed. Nothing reads AWS
keys — creds come from the Lambda role's ambient chain (IAM role only).
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Optional, Protocol

from backend import config


class BedrockError(RuntimeError):
    """Raised when a Bedrock call (or every model/account in a route) fails."""


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


def default_client_factory(service: str, *, creds: Optional[Credentials] = None,
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


def bedrock_for_account(
    account_id: str, role_name: str, external_id: str, client_factory: ClientFactory
) -> _BedrockClient:
    creds = _assume(account_id, role_name, external_id, client_factory)
    return client_factory("bedrock-runtime", creds=creds, region=config.BEDROCK_REGION)
