from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
import base64
import hashlib
import hmac
import json
import os
import re
import secrets
from threading import RLock
from typing import Any, Mapping


MOBILE_AUTH_VERSION = "mobile-auth-v1.0.0"
_JWT_ISSUER = "pangu-tianji"
_JWT_AUDIENCE = "pangu-mobile"
_DEVICE_NAME_PATTERN = re.compile(r"^[^\x00-\x1f\x7f]{1,80}$")


class MobileAuthError(RuntimeError):
    """Carry a stable API error code for mobile authentication failures."""

    def __init__(self, code: str, message: str, status_code: int = 401) -> None:
        super().__init__(message)
        self.code = code
        self.status_code = status_code


@dataclass(frozen=True)
class MobilePrincipal:
    """Represent one verified mobile JWT without exposing the raw token."""

    subject: str
    device_id: str
    device_name: str
    role: str
    scopes: tuple[str, ...]
    issued_at: str
    expires_at: str
    token_id: str


@dataclass
class _PairingCode:
    """Keep one short-lived pairing challenge in memory using a salted hash."""

    pairing_id: str
    salt: bytes
    code_hash: bytes
    created_at: datetime
    expires_at: datetime
    attempts_left: int
    role: str
    used: bool = False


class MobileAuthService:
    """Issue and verify bounded mobile JWTs without persisting authentication secrets."""

    ADMIN_SCOPES = (
        "mobile:read",
        "mobile:copilot",
        "mobile:journal",
        "mobile:notifications",
    )
    VIEWER_SCOPES = ("mobile:read",)

    def __init__(
        self,
        *,
        enabled: bool,
        config: Mapping[str, Any] | None = None,
        secret: str | bytes | None = None,
        now_provider: Any | None = None,
    ) -> None:
        """Configure ephemeral pairing and JWT policy.

        The JWT signing secret is read from ``PANGU_MOBILE_JWT_SECRET`` when no
        explicit test-only secret is supplied. If absent, a process-local random
        secret is used and the status contract clearly marks tokens as ephemeral.
        """
        self.enabled = bool(enabled)
        self.config = dict(config or {})
        self._now_provider = now_provider or (lambda: datetime.now(timezone.utc))
        configured_secret = secret
        if configured_secret is None:
            configured_secret = os.getenv("PANGU_MOBILE_JWT_SECRET", "")
        if isinstance(configured_secret, str):
            configured_secret = configured_secret.encode("utf-8")
        self.secret_persistence = "environment"
        if configured_secret and len(configured_secret) < 32:
            if self.enabled:
                raise MobileAuthError(
                    "MOBILE_SECRET_TOO_SHORT",
                    "PANGU_MOBILE_JWT_SECRET至少需要32字节",
                    503,
                )
            # Disabled desktop mode must not fail because of an unused env typo.
            configured_secret = None
        if not configured_secret:
            configured_secret = secrets.token_bytes(32)
            self.secret_persistence = "ephemeral"
        self._secret = bytes(configured_secret)
        self.token_ttl_seconds = self._bounded_int(
            "token_ttl_minutes", 720, 5, 1440
        ) * 60
        self.pairing_ttl_seconds = self._bounded_int(
            "pairing_ttl_seconds", 300, 60, 900
        )
        self.pairing_max_attempts = self._bounded_int(
            "pairing_max_attempts", 5, 1, 10
        )
        self._pairings: dict[str, _PairingCode] = {}
        self._failed_by_client: dict[str, list[float]] = {}
        self._lock = RLock()

    def status(self) -> dict[str, Any]:
        """Return capability state without revealing keys, codes, or token material."""
        message = "移动助手未启用，请使用server.py --mobile启动"
        if self.enabled and self.secret_persistence == "ephemeral":
            message = "移动助手已启用；JWT使用运行期密钥，服务重启后旧令牌失效"
        elif self.enabled:
            message = "移动助手已启用；JWT密钥来自进程环境"
        return {
            "service_version": MOBILE_AUTH_VERSION,
            "enabled": self.enabled,
            "ready": self.enabled,
            "pairing_available": self.enabled,
            "secret_persistence": self.secret_persistence,
            "restart_invalidates_tokens": self.secret_persistence == "ephemeral",
            "token_ttl_seconds": self.token_ttl_seconds,
            "pairing_ttl_seconds": self.pairing_ttl_seconds,
            "message": message,
            "can_trade": False,
            "can_create_orders": False,
        }

    def create_pairing_code(self, role: str = "admin") -> dict[str, Any]:
        """Create one single-use numeric challenge for a loopback administrator."""
        self._require_enabled()
        if role not in {"viewer", "admin"}:
            raise MobileAuthError("MOBILE_ROLE_INVALID", "配对角色无效", 422)
        now = self._now()
        pairing_id = f"pair-{secrets.token_hex(12)}"
        code = f"{secrets.randbelow(100_000_000):08d}"
        salt = secrets.token_bytes(16)
        record = _PairingCode(
            pairing_id=pairing_id,
            salt=salt,
            code_hash=self._pairing_digest(pairing_id, code, salt),
            created_at=now,
            expires_at=now + timedelta(seconds=self.pairing_ttl_seconds),
            attempts_left=self.pairing_max_attempts,
            role=role,
        )
        with self._lock:
            self._purge_pairings(now)
            # A personal workstation needs only one active enrollment window.
            self._pairings.clear()
            self._pairings[pairing_id] = record
        return {
            "pairing_id": pairing_id,
            "pairing_code": code,
            "expires_at": record.expires_at.isoformat(),
            "expires_in_seconds": self.pairing_ttl_seconds,
            "attempts_allowed": self.pairing_max_attempts,
            "role": role,
            "single_use": True,
            "can_trade": False,
            "can_create_orders": False,
        }

    def pair_device(
        self,
        *,
        pairing_id: str | None,
        pairing_code: str,
        device_name: str,
        client_host: str,
    ) -> dict[str, Any]:
        """Consume one pairing challenge and issue a least-privilege mobile JWT."""
        self._require_enabled()
        normalized_name = device_name.strip()
        if not _DEVICE_NAME_PATTERN.fullmatch(normalized_name):
            raise MobileAuthError(
                "INVALID_DEVICE_NAME", "设备名称必须为1至80个可见字符", 422
            )
        client_key = str(client_host or "unknown")[:128]
        normalized_code = str(pairing_code)
        if len(normalized_code) != 8 or not normalized_code.isdigit():
            raise MobileAuthError(
                "PAIRING_CODE_FORMAT", "配对码必须为8位数字", 422
            )
        now = self._now()
        now_ts = now.timestamp()
        with self._lock:
            self._enforce_client_rate_limit(client_key, now_ts)
            selected_id = str(pairing_id or "")
            if not selected_id and len(self._pairings) == 1:
                selected_id = next(iter(self._pairings))
            record = self._pairings.get(selected_id)
            self._purge_pairings(now, preserve=selected_id)
            if record is None:
                self._record_failure(client_key, now_ts)
                raise MobileAuthError(
                    "PAIRING_CODE_INVALID", "配对码无效或已过期", 401
                )
            if record.used:
                raise MobileAuthError(
                    "PAIRING_CODE_USED", "配对码已使用", 409
                )
            if record.expires_at <= now:
                self._pairings.pop(selected_id, None)
                raise MobileAuthError(
                    "PAIRING_CODE_EXPIRED", "配对码已过期", 410
                )
            supplied = self._pairing_digest(selected_id, normalized_code, record.salt)
            if not hmac.compare_digest(supplied, record.code_hash):
                record.attempts_left -= 1
                self._record_failure(client_key, now_ts)
                if record.attempts_left <= 0:
                    self._pairings.pop(selected_id, None)
                    raise MobileAuthError(
                        "PAIRING_ATTEMPTS_EXHAUSTED", "配对尝试次数已用尽", 429
                    )
                raise MobileAuthError(
                    "PAIRING_CODE_INVALID",
                    f"配对码错误，剩余{record.attempts_left}次尝试",
                    401,
                )
            record.used = True
            self._pairings.pop(selected_id, None)
            self._failed_by_client.pop(client_key, None)

        device_id = f"device-{secrets.token_hex(12)}"
        subject = "personal-investor"
        expires = now + timedelta(seconds=self.token_ttl_seconds)
        scopes = self.ADMIN_SCOPES if record.role == "admin" else self.VIEWER_SCOPES
        claims = {
            "iss": _JWT_ISSUER,
            "aud": _JWT_AUDIENCE,
            "sub": subject,
            "iat": int(now.timestamp()),
            "nbf": int(now.timestamp()),
            "exp": int(expires.timestamp()),
            "jti": f"jwt-{secrets.token_hex(16)}",
            "role": record.role,
            "device_id": device_id,
            "device_name": normalized_name,
            "scopes": list(scopes),
        }
        return {
            "access_token": self._encode_jwt(claims),
            "token_type": "bearer",
            "expires_in_seconds": self.token_ttl_seconds,
            "expires_at": expires.isoformat(),
            "subject": subject,
            "device_id": device_id,
            "role": record.role,
            "scopes": list(scopes),
            "safety": self._safety(),
            "can_trade": False,
            "can_create_orders": False,
        }

    def authenticate(self, authorization: str | None) -> MobilePrincipal:
        """Validate one Bearer JWT and return only normalized authorization claims."""
        self._require_enabled()
        if not authorization or not authorization.startswith("Bearer "):
            raise MobileAuthError(
                "MOBILE_TOKEN_REQUIRED", "需要移动助手访问令牌", 401
            )
        token = authorization[7:].strip()
        if not token or len(token) > 4096:
            raise MobileAuthError("MOBILE_TOKEN_INVALID", "令牌长度无效", 401)
        claims = self._decode_jwt(token)
        scopes = claims.get("scopes")
        allowed_scopes = set(self.ADMIN_SCOPES)
        if not isinstance(scopes, list) or not allowed_scopes.issuperset(scopes):
            raise MobileAuthError("MOBILE_TOKEN_SCOPE_INVALID", "令牌权限无效", 403)
        role = str(claims.get("role") or "")
        expected_scopes = (
            set(self.ADMIN_SCOPES) if role == "admin" else set(self.VIEWER_SCOPES)
        )
        if role not in {"viewer", "admin"} or set(scopes) != expected_scopes:
            raise MobileAuthError("MOBILE_TOKEN_ROLE_INVALID", "令牌角色无效", 403)
        issued = datetime.fromtimestamp(int(claims["iat"]), timezone.utc)
        expires = datetime.fromtimestamp(int(claims["exp"]), timezone.utc)
        return MobilePrincipal(
            subject=str(claims["sub"]),
            device_id=str(claims["device_id"]),
            device_name=str(claims.get("device_name") or "移动设备"),
            role=str(claims["role"]),
            scopes=tuple(str(item) for item in scopes),
            issued_at=issued.isoformat(),
            expires_at=expires.isoformat(),
            token_id=str(claims["jti"]),
        )

    def session(self, principal: MobilePrincipal) -> dict[str, Any]:
        """Serialize an authenticated session without returning the bearer token."""
        return {
            **asdict(principal),
            "scopes": list(principal.scopes),
            "safety": self._safety(),
            "can_trade": False,
            "can_create_orders": False,
        }

    def _encode_jwt(self, claims: Mapping[str, Any]) -> str:
        """Create a compact HS256 JWT using only the Python standard library."""
        header = {"alg": "HS256", "typ": "JWT"}
        segments = [self._json_segment(header), self._json_segment(dict(claims))]
        signing_input = ".".join(segments).encode("ascii")
        signature = hmac.new(self._secret, signing_input, hashlib.sha256).digest()
        return f"{segments[0]}.{segments[1]}.{self._b64encode(signature)}"

    def _decode_jwt(self, token: str) -> dict[str, Any]:
        """Verify signature, standard claims, role and device identity."""
        try:
            header_segment, payload_segment, signature_segment = token.split(".")
            if any(
                not re.fullmatch(r"[A-Za-z0-9_-]+", segment)
                for segment in (header_segment, payload_segment, signature_segment)
            ):
                raise ValueError("non-canonical JWT segment")
            header = json.loads(self._b64decode(header_segment))
            claims = json.loads(self._b64decode(payload_segment))
            signature = self._b64decode_bytes(signature_segment)
        except (ValueError, TypeError, json.JSONDecodeError, UnicodeDecodeError) as exc:
            raise MobileAuthError("MOBILE_TOKEN_INVALID", "令牌格式无效", 401) from exc
        if header != {"alg": "HS256", "typ": "JWT"}:
            raise MobileAuthError("MOBILE_TOKEN_INVALID", "令牌算法无效", 401)
        expected = hmac.new(
            self._secret,
            f"{header_segment}.{payload_segment}".encode("ascii"),
            hashlib.sha256,
        ).digest()
        if not hmac.compare_digest(expected, signature):
            raise MobileAuthError("MOBILE_TOKEN_INVALID", "令牌签名无效", 401)
        required = {
            "iss", "aud", "sub", "iat", "nbf", "exp", "jti", "role", "device_id"
        }
        if not required.issubset(claims):
            raise MobileAuthError("MOBILE_TOKEN_INVALID", "令牌声明不完整", 401)
        if claims["iss"] != _JWT_ISSUER or claims["aud"] != _JWT_AUDIENCE:
            raise MobileAuthError("MOBILE_TOKEN_INVALID", "令牌签发方或受众无效", 401)
        try:
            issued = int(claims["iat"])
            not_before = int(claims["nbf"])
            expires = int(claims["exp"])
        except (TypeError, ValueError) as exc:
            raise MobileAuthError("MOBILE_TOKEN_INVALID", "令牌时间声明无效", 401) from exc
        now = int(self._now().timestamp())
        if issued > now + 30 or not_before > now + 30:
            raise MobileAuthError("MOBILE_TOKEN_NOT_ACTIVE", "令牌尚未生效", 401)
        if expires <= now:
            raise MobileAuthError("MOBILE_TOKEN_EXPIRED", "令牌已过期", 401)
        if expires <= issued or expires - issued > self.token_ttl_seconds + 30:
            raise MobileAuthError("MOBILE_TOKEN_INVALID", "令牌有效期无效", 401)
        if not str(claims["device_id"]).startswith("device-"):
            raise MobileAuthError("MOBILE_TOKEN_INVALID", "令牌设备声明无效", 401)
        return claims

    def _enforce_client_rate_limit(self, client_key: str, now_ts: float) -> None:
        """Block brute-force pairing after repeated failures in five minutes."""
        cutoff = now_ts - 300.0
        attempts = [item for item in self._failed_by_client.get(client_key, []) if item >= cutoff]
        self._failed_by_client[client_key] = attempts
        if len(attempts) >= 10:
            raise MobileAuthError(
                "PAIRING_RATE_LIMITED", "配对失败次数过多，请稍后再试", 429
            )

    def _record_failure(self, client_key: str, now_ts: float) -> None:
        """Record one failed exchange without persisting a client network address."""
        self._failed_by_client.setdefault(client_key, []).append(now_ts)

    def _purge_pairings(self, now: datetime, preserve: str | None = None) -> None:
        """Remove expired or consumed challenges before every pairing operation."""
        for key, record in list(self._pairings.items()):
            if key != preserve and (record.used or record.expires_at <= now):
                self._pairings.pop(key, None)

    def _pairing_digest(self, pairing_id: str, code: str, salt: bytes) -> bytes:
        """Hash a low-entropy pairing code with a secret-keyed salt context."""
        payload = salt + pairing_id.encode("utf-8") + str(code).encode("utf-8")
        return hmac.new(self._secret, payload, hashlib.sha256).digest()

    def _require_enabled(self) -> None:
        """Fail closed when mobile mode was not explicitly enabled at startup."""
        if not self.enabled:
            raise MobileAuthError(
                "MOBILE_ASSISTANT_DISABLED",
                "移动助手未启用，请使用server.py --mobile启动",
                503,
            )

    def _now(self) -> datetime:
        """Return a timezone-aware UTC clock value for deterministic tests."""
        value = self._now_provider()
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc)

    def _bounded_int(self, key: str, default: int, minimum: int, maximum: int) -> int:
        """Read a bounded integer setting and reject unsafe configuration drift."""
        try:
            value = int(self.config.get(key, default))
        except (TypeError, ValueError) as exc:
            raise MobileAuthError(
                "MOBILE_CONFIG_INVALID", f"{key}配置无效", 503
            ) from exc
        if not minimum <= value <= maximum:
            raise MobileAuthError(
                "MOBILE_CONFIG_INVALID",
                f"{key}必须位于{minimum}至{maximum}之间",
                503,
            )
        return value

    @staticmethod
    def _json_segment(payload: Mapping[str, Any]) -> str:
        """Encode one canonical JSON JWT segment without padding."""
        raw = json.dumps(
            payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        return MobileAuthService._b64encode(raw)

    @staticmethod
    def _b64encode(raw: bytes) -> str:
        """Return URL-safe base64 without JWT padding."""
        return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")

    @staticmethod
    def _b64decode(value: str) -> str:
        """Decode one UTF-8 JWT segment with restored base64 padding."""
        return MobileAuthService._b64decode_bytes(value).decode("utf-8")

    @staticmethod
    def _b64decode_bytes(value: str) -> bytes:
        """Decode one URL-safe base64 value and reject non-ASCII input."""
        padding = "=" * (-len(value) % 4)
        return base64.urlsafe_b64decode((value + padding).encode("ascii"))

    @staticmethod
    def _safety() -> dict[str, bool]:
        """Return the immutable non-trading capability boundary for mobile clients."""
        return {
            "live_trading_enabled": False,
            "can_trade": False,
            "can_create_orders": False,
            "can_modify_strategy": False,
            "can_modify_portfolio": False,
            "can_modify_risk": False,
        }
