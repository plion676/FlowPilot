"""Only operator-approved IP URLs and credential profiles may create clients."""

import ipaddress
import json
import os
import re
from collections.abc import Mapping
from urllib.parse import urlsplit

from app.clients.endpoints import Endpoint
from app.clients.mcp import OfficialMcpClient
from app.core.errors import AppError


class McpClientFactory:
    def __init__(self, source: Mapping[str, str] | None = None):
        env = os.environ if source is None else source
        default_url = env.get("MCP_URL", "http://127.0.0.1:3100/mcp/consultant")
        targets = json.loads(
            env.get(
                "MCP_ALLOWED_TARGETS",
                json.dumps(
                    [
                        default_url,
                        "http://127.0.0.1:3100/mcp/consultant",
                    ]
                ),
            )
        )
        if (
            not isinstance(targets, list)
            or len(targets) > 10
            or any(not isinstance(target, str) for target in targets)
        ):
            raise ValueError("MCP_ALLOWED_TARGETS 必须为 URL 数组")
        self.targets = tuple(dict.fromkeys(targets))
        profiles = json.loads(
            env.get(
                "MCP_CREDENTIAL_PROFILES",
                json.dumps(
                    {
                        "local": {
                            "secret_env": "MCP_CALL_SECRET",
                            "endpoint_ids": ["business"],
                            "targets": list(self.targets),
                        }
                    }
                ),
            )
        )
        self._profiles = {}
        if not isinstance(profiles, dict):
            raise ValueError("MCP 凭据 Profile 必须为对象")
        for name, profile in profiles.items():
            if not re.fullmatch(r"[a-z][a-z0-9_]{0,31}", name) or not isinstance(profile, dict):
                raise ValueError("MCP 凭据 Profile 格式无效")
            secret_env = profile.get("secret_env", "")
            if not re.fullmatch(
                r"MCP_CALL_SECRET|OPSPILOT_MCP_SECRET_[A-Z0-9_]+", secret_env
            ) or set(profile) != {"secret_env", "endpoint_ids", "targets"}:
                raise ValueError("MCP 凭据 Profile 格式无效")
            if (
                not isinstance(profile["endpoint_ids"], list)
                or not isinstance(profile["targets"], list)
                or any(
                    not isinstance(value, str)
                    for value in [*profile["endpoint_ids"], *profile["targets"]]
                )
            ):
                raise ValueError("MCP 凭据 Profile 范围必须为字符串数组")
            self._profiles[name] = (
                env.get(secret_env, ""),
                tuple(profile["endpoint_ids"]),
                tuple(profile["targets"]),
            )

    def validate(self, endpoint: Endpoint) -> None:
        try:
            parsed = urlsplit(endpoint.url)
            address = ipaddress.ip_address(parsed.hostname or "")
            valid = (address.is_loopback or address.is_private) and not (
                address.is_link_local or address.is_unspecified or address.is_multicast
            )
            _ = parsed.port  # Reject malformed ports without printing the URL.
        except ValueError:
            raise AppError("INVALID_ENDPOINT_TARGET", "MCP 连接地址格式无效。", 400) from None
        if (
            not valid
            or endpoint.url not in self.targets
            or parsed.scheme not in {"http", "https"}
            or parsed.username
            or parsed.password
            or parsed.query
            or parsed.fragment
            or "?" in endpoint.url
            or "#" in endpoint.url
            or "%" in endpoint.url
            or not parsed.path
            or ".." in parsed.path
            or "//" in parsed.path
            or "\\" in endpoint.url
        ):
            raise AppError(
                "INVALID_ENDPOINT_TARGET", "只允许服务端白名单内的明确 IP 连接地址。", 400
            )
        profile = self._profiles.get(endpoint.credential_profile)
        if not profile or endpoint.id not in profile[1] or endpoint.url not in profile[2]:
            raise AppError("INVALID_CREDENTIAL_PROFILE", "凭据 Profile 不允许用于这个连接。", 400)

    def configured_profiles(self) -> list[dict]:
        return [
            {"id": name, "configured": len(value[0]) >= 32}
            for name, value in self._profiles.items()
        ]

    def for_endpoint(self, endpoint: Endpoint):
        self.validate(endpoint)
        if not endpoint.enabled:
            raise AppError("ENDPOINT_DISABLED", "MCP 连接已停用。", 403)
        secret = self._profiles[endpoint.credential_profile][0]
        if len(secret) < 32:
            raise AppError("MCP_CREDENTIAL_NOT_CONFIGURED", "MCP 连接的服务端密钥尚未配置。", 503)
        return OfficialMcpClient(endpoint.url, secret, endpoint_id=endpoint.id)
