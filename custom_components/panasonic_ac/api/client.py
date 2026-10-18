"""Self-contained async client for the Panasonic Comfort Cloud API.

No Home Assistant imports and no third-party dependencies beyond aiohttp, so it
can be tested (and debugged) on its own.

Self-healing behaviour, aimed at the ways other integrations have broken:
- Refresh tokens rotate; every new token set is reported through
  ``on_session_update`` so it can be persisted (a restart never reuses a stale one).
- A rejected refresh token (``invalid_grant``) falls back to a full login.
- Repeated auth errors on API calls also force a full login.
- Error 4106 (app version outdated) re-detects the app version and retries.
- Error 4103 (updated terms) accepts them automatically if allowed, otherwise
  raises ``AgreementRequired`` with a clear message.
"""

from __future__ import annotations

import asyncio
import base64
import contextlib
import hashlib
import json
import logging
import re
import secrets
import time
from collections.abc import Callable
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from html.parser import HTMLParser
from typing import Any
from urllib.parse import parse_qs, quote_plus, urljoin, urlparse

import aiohttp

from . import const
from .exceptions import (
    AgreementRequired,
    ApiError,
    CannotConnect,
    InvalidAuth,
    InvalidMfaCode,
    MfaRequired,
)
from .models import AcDevice

_LOGGER = logging.getLogger(__name__)

REQUEST_TIMEOUT = aiohttp.ClientTimeout(total=30)
APP_VERSION_MAX_AGE = 24 * 3600
# After a live status read fails, use cached status for this many polls
CACHED_POLLS_AFTER_LIVE_FAILURE = 10
# Consecutive auth failures on API calls before forcing a full login
AUTH_FAILURES_BEFORE_LOGIN = 2


# --------------------------------------------------------------------- helpers
def api_timestamp(epoch: float | None = None) -> str:
    """X-APP-TIMESTAMP value (UTC)."""
    t = time.time() if epoch is None else epoch
    return datetime.fromtimestamp(t, tz=UTC).strftime("%Y-%m-%d %H:%M:%S")


def api_key(timestamp: str, access_token: str) -> str:
    """X-CFC-API-KEY: sha256 over app name, fixed key, ms timestamp and bearer token."""
    ms = int(datetime.strptime(timestamp, "%Y-%m-%d %H:%M:%S").replace(tzinfo=UTC).timestamp() * 1000)
    digest = hashlib.sha256(
        b"".join(
            [
                const.APP_NAME.encode(),
                const.API_SIGNING_KEY.encode(),
                str(ms).encode(),
                b"Bearer ",
                access_token.encode(),
            ]
        )
    ).hexdigest()
    return digest[:9] + "cfc" + digest[9:]


def _pkce_pair() -> tuple[str, str]:
    verifier = secrets.token_urlsafe(32)
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).decode().rstrip("=")
    return verifier, challenge


def _query(url: str, key: str) -> str | None:
    return parse_qs(urlparse(url).query).get(key, [None])[0]


def _is_app_redirect(url: str) -> bool:
    return urlparse(url).scheme not in ("http", "https", "")


def _version_tuple(version: str) -> tuple[int, ...]:
    try:
        return tuple(int(p) for p in version.split("."))
    except ValueError:
        return ()


def _error_code(text: str) -> str | None:
    """Extract the Comfort Cloud error code from an error body."""
    try:
        data = json.loads(text)
        if isinstance(data, dict) and data.get("code") is not None:
            return str(data["code"])
    except ValueError:
        pass
    match = re.search(r"\b(41\d\d|43\d\d)\b", text)
    return match.group(1) if match else None


def prepare_guid(guid: str) -> str:
    """Device GUIDs go into URLs with '/' replaced by 'f' (as the app does)."""
    return quote_plus(guid.replace("/", "f"))


def utc_offset(now: datetime) -> str:
    offset = now.utcoffset()
    minutes = int(offset.total_seconds() // 60) if offset else 0
    sign = "+" if minutes >= 0 else "-"
    minutes = abs(minutes)
    return f"{sign}{minutes // 60:02d}:{minutes % 60:02d}"


class _FormParser(HTMLParser):
    """Collects hidden inputs and the first form action from an HTML page."""

    def __init__(self) -> None:
        super().__init__()
        self.fields: dict[str, str] = {}
        self.action: str | None = None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        a = dict(attrs)
        if tag == "form" and self.action is None:
            self.action = a.get("action")
        elif tag == "input" and a.get("type") == "hidden" and a.get("name"):
            self.fields[a["name"]] = a.get("value") or ""


def _parse_form(html: str) -> _FormParser:
    parser = _FormParser()
    parser.feed(html)
    return parser


_GUARDIAN_KEYS = ("postActionURL", "serviceUrl", "requestToken")


def _guardian_config(html: str) -> dict[str, str] | None:
    """Auth0 Guardian MFA page embeds ``window.__g_config = {...}`` (not valid JSON)."""
    if "__g_config" not in html:
        return None
    config: dict[str, str] = {}
    for key in _GUARDIAN_KEYS:
        match = re.search(rf'{key}\s*:\s*"((?:[^"\\]|\\.)*)"', html)
        if match:
            config[key] = match.group(1).replace("\\/", "/")
    return config if all(k in config for k in _GUARDIAN_KEYS) else None


@dataclass
class _Response:
    status: int
    headers: dict[str, str]
    text: str
    url: str

    @property
    def location(self) -> str | None:
        return self.headers.get("Location") or self.headers.get("location")


@dataclass
class SessionData:
    """Everything needed to resume without logging in again."""

    access_token: str | None = None
    refresh_token: str | None = None
    expires_at: float = 0
    client_id: str | None = None

    @classmethod
    def from_dict(cls, data: dict[str, Any] | None) -> SessionData:
        data = data or {}
        return cls(
            access_token=data.get("access_token"),
            refresh_token=data.get("refresh_token"),
            expires_at=float(data.get("expires_at") or 0),
            client_id=data.get("client_id"),
        )

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)

    @property
    def access_valid(self) -> bool:
        return bool(self.access_token) and time.time() < self.expires_at - 60


@dataclass
class _PendingMfa:
    code_verifier: str
    mfa_token: str | None = None
    guardian: dict[str, str] | None = None


# ---------------------------------------------------------------------- client
class PanasonicClient:
    """Comfort Cloud client for air conditioners."""

    def __init__(
        self,
        session: aiohttp.ClientSession,
        username: str,
        password: str,
        *,
        stored_session: dict[str, Any] | None = None,
        app_version: str | None = None,
        auto_accept_agreements: bool = False,
        on_session_update: Callable[[dict[str, Any]], None] | None = None,
        auth_base: str = const.AUTH_BASE,
        api_base: str = const.API_BASE,
        play_store_url: str = const.PLAY_STORE_URL,
    ) -> None:
        self._http = session
        self._username = username
        self._password = password
        self._data = SessionData.from_dict(stored_session)
        self._app_version_override = (app_version or "").strip() or None
        self._detected_app_version: str | None = None
        self._app_version_checked = 0.0
        self.auto_accept_agreements = auto_accept_agreements
        self._on_session_update = on_session_update
        self._auth_base = auth_base.rstrip("/")
        self._api_base = api_base.rstrip("/")
        self._play_store_url = play_store_url
        self._lock = asyncio.Lock()
        self._pending_mfa: _PendingMfa | None = None
        self._auth_failures = 0
        self._cached_polls: dict[str, int] = {}

    # ------------------------------------------------------------ properties
    @property
    def session_data(self) -> dict[str, Any]:
        return self._data.as_dict()

    @property
    def app_version(self) -> str:
        return self._app_version_override or self._detected_app_version or const.APP_VERSION_FALLBACK

    @property
    def mfa_pending(self) -> bool:
        return self._pending_mfa is not None

    def set_app_version_override(self, version: str | None) -> None:
        self._app_version_override = (version or "").strip() or None

    # ------------------------------------------------------------- transport
    async def _fetch(self, method: str, url: str, **kwargs: Any) -> _Response:
        kwargs.setdefault("allow_redirects", False)
        kwargs.setdefault("timeout", REQUEST_TIMEOUT)
        try:
            async with self._http.request(method, url, **kwargs) as resp:
                text = await resp.text(errors="replace")
                return _Response(resp.status, dict(resp.headers), text, str(resp.url))
        except (aiohttp.ClientError, TimeoutError) as err:
            raise CannotConnect(f"{method} {urlparse(url).path}: {err}") from err

    def _auth_headers(self) -> dict[str, str]:
        return {"Auth0-Client": const.AUTH0_CLIENT, "User-Agent": const.AUTH_API_USER_AGENT}

    async def _api_headers(self, *, include_client_id: bool = True) -> dict[str, str]:
        ts = api_timestamp()
        token = self._data.access_token or ""
        headers = {
            "Accept": "application/json; charset=utf-8",
            "Content-Type": "application/json",
            "User-Agent": const.API_USER_AGENT,
            "X-APP-NAME": const.APP_NAME,
            "X-APP-TIMESTAMP": ts,
            "X-APP-TYPE": "1",
            "X-APP-VERSION": await self._get_app_version(),
            "X-CFC-API-KEY": api_key(ts, token),
            "X-User-Authorization-V2": f"Bearer {token}",
        }
        if include_client_id and self._data.client_id:
            headers["X-Client-Id"] = self._data.client_id
        return headers

    # ----------------------------------------------------------- app version
    async def _get_app_version(self, force: bool = False) -> str:
        if self._app_version_override:
            return self._app_version_override
        if force or time.time() - self._app_version_checked > APP_VERSION_MAX_AGE:
            self._app_version_checked = time.time()
            detected = await self._detect_app_version()
            if detected:
                self._detected_app_version = detected
        return self.app_version

    async def _detect_app_version(self) -> str | None:
        try:
            resp = await self._fetch("GET", self._play_store_url, allow_redirects=True)
        except CannotConnect as err:
            _LOGGER.debug("Could not reach the Play Store for the app version: %s", err)
            return None
        match = re.search(r'\[\[\["(\d+\.\d+\.\d+)"\]\]', resp.text) or re.search(
            r'\["(\d+\.\d+\.\d+)"\]', resp.text
        )
        if not match:
            _LOGGER.debug("No app version found on the Play Store page")
            return None
        version = match.group(1)
        if _version_tuple(version) < _version_tuple(const.APP_VERSION_FALLBACK):
            _LOGGER.debug("Ignoring implausible Play Store version %s", version)
            return None
        _LOGGER.debug("Detected Comfort Cloud app version %s", version)
        return version

    # ------------------------------------------------------------------ auth
    def _store_tokens(self, token: dict[str, Any], received_at: float) -> None:
        if "access_token" not in token:
            raise InvalidAuth("Token response did not contain an access token")
        self._data.access_token = token["access_token"]
        if token.get("refresh_token"):
            self._data.refresh_token = token["refresh_token"]
        self._data.expires_at = received_at + float(token.get("expires_in", 3600))
        self._notify()

    def _notify(self) -> None:
        if self._on_session_update:
            try:
                self._on_session_update(self.session_data)
            except Exception:  # noqa: BLE001 - never let persistence break the client
                _LOGGER.exception("Failed to persist session data")

    async def async_login(self) -> None:
        """Full username/password login. Raises MfaRequired if a 2FA code is needed."""
        async with self._lock:
            await self._login()

    async def async_verify_mfa(self, code: str) -> None:
        """Finish a login that raised MfaRequired."""
        async with self._lock:
            pending = self._pending_mfa
            if pending is None:
                raise InvalidAuth("No 2FA challenge is pending; start the login again")
            code = re.sub(r"\s", "", code)
            if pending.guardian:
                await self._verify_guardian(pending, code)
            else:
                await self._verify_classic_mfa(pending, code)
            self._pending_mfa = None
            await self._retrieve_client_id()
            self._auth_failures = 0

    async def _login(self) -> None:
        _LOGGER.debug("Starting full login")
        self._pending_mfa = None
        self._http.cookie_jar.clear_domain(urlparse(self._auth_base).hostname or "")
        verifier, challenge = _pkce_pair()
        state = secrets.token_urlsafe(16)

        resp = await self._fetch(
            "GET",
            f"{self._auth_base}/authorize",
            headers={"User-Agent": const.AUTH_API_USER_AGENT},
            params={
                "scope": const.SCOPE,
                "audience": const.AUDIENCE,
                "protocol": "oauth2",
                "response_type": "code",
                "code_challenge": challenge,
                "code_challenge_method": "S256",
                "auth0Client": const.AUTH0_CLIENT,
                "client_id": const.APP_CLIENT_ID,
                "redirect_uri": const.REDIRECT_URI,
                "state": state,
            },
        )
        if resp.status != 302 or not resp.location:
            raise ApiError("authorize", resp.status, resp.text)

        if _is_app_redirect(resp.location):
            code = await self._follow_for_code(resp.location, resp.url, verifier)
        else:
            code = await self._submit_credentials(resp, verifier)

        await self._exchange_code(code, verifier)
        await self._retrieve_client_id()
        self._auth_failures = 0

    async def _submit_credentials(self, authorize: _Response, verifier: str) -> str:
        login_page_url = urljoin(authorize.url, authorize.location or "")
        state = _query(login_page_url, "state")
        page = await self._fetch("GET", login_page_url)
        if page.status != 200:
            raise ApiError("login_page", page.status, page.text)

        csrf = None
        for cookie in self._http.cookie_jar:
            if cookie.key == "_csrf":
                csrf = cookie.value
        if csrf is None:
            match = re.search(r"_csrf=([^;]+)", page.headers.get("Set-Cookie", ""))
            csrf = match.group(1) if match else ""

        resp = await self._fetch(
            "POST",
            f"{self._auth_base}/usernamepassword/login",
            headers=self._auth_headers(),
            json={
                "client_id": const.APP_CLIENT_ID,
                "redirect_uri": const.REDIRECT_URI,
                "tenant": const.AUTH_TENANT,
                "response_type": "code",
                "scope": const.SCOPE,
                "audience": const.AUDIENCE,
                "_csrf": csrf,
                "state": state,
                "_intstate": "deprecated",
                "username": self._username,
                "password": self._password,
                "lang": "en",
                "connection": const.AUTH_CONNECTION,
            },
        )
        if resp.status in (400, 401, 403, 429):
            raise InvalidAuth(self._auth0_error(resp.text) or f"Login rejected (HTTP {resp.status})")
        if resp.status != 200:
            raise ApiError("login", resp.status, resp.text)

        self._detect_mfa(resp.text, verifier)
        form = _parse_form(resp.text)
        if not form.fields:
            raise ApiError("login", resp.status, "No callback form in login response")

        callback_url = urljoin(f"{self._auth_base}/", form.action or "/login/callback")
        callback = await self._fetch(
            "POST",
            callback_url,
            data=form.fields,
            headers={"User-Agent": const.AUTH_BROWSER_USER_AGENT},
        )
        if callback.status not in (301, 302, 303) or not callback.location:
            raise ApiError("login_callback", callback.status, callback.text)
        return await self._follow_for_code(callback.location, callback.url, verifier)

    async def _follow_for_code(self, location: str, from_url: str, verifier: str) -> str:
        """Follow Auth0 redirects until the app redirect URI carries the code."""
        for _ in range(8):
            if _is_app_redirect(location):
                code = _query(location, "code")
                if code:
                    return code
                raise InvalidAuth(_query(location, "error_description") or "Login did not return a code")
            url = urljoin(from_url, location)
            resp = await self._fetch("GET", url, headers={"User-Agent": const.AUTH_BROWSER_USER_AGENT})
            if resp.status in (301, 302, 303) and resp.location:
                from_url, location = url, resp.location
                continue
            if resp.status == 200:
                self._detect_mfa(resp.text, verifier)
            raise ApiError("login_redirect", resp.status, resp.text)
        raise CannotConnect("Too many redirects during login")

    def _detect_mfa(self, html: str, verifier: str) -> None:
        form = _parse_form(html)
        if form.fields.get("mfa_token"):
            self._pending_mfa = _PendingMfa(verifier, mfa_token=form.fields["mfa_token"])
            raise MfaRequired
        guardian = _guardian_config(html)
        if guardian:
            self._pending_mfa = _PendingMfa(verifier, guardian=guardian)
            raise MfaRequired

    @staticmethod
    def _auth0_error(text: str) -> str | None:
        try:
            data = json.loads(text)
        except ValueError:
            return None
        if isinstance(data, dict):
            return data.get("description") or data.get("error_description") or data.get("message")
        return None

    async def _exchange_code(self, code: str, verifier: str) -> None:
        received = time.time()
        resp = await self._fetch(
            "POST",
            f"{self._auth_base}/oauth/token",
            headers=self._auth_headers(),
            json={
                "scope": "openid",
                "client_id": const.APP_CLIENT_ID,
                "grant_type": "authorization_code",
                "code": code,
                "redirect_uri": const.REDIRECT_URI,
                "code_verifier": verifier,
            },
        )
        if resp.status != 200:
            raise InvalidAuth(self._auth0_error(resp.text) or f"Token exchange failed (HTTP {resp.status})")
        self._store_tokens(json.loads(resp.text), received)

    async def _verify_classic_mfa(self, pending: _PendingMfa, code: str) -> None:
        # Best-effort challenge; plain OTP enrollments don't strictly need it.
        with contextlib.suppress(CannotConnect):
            await self._fetch(
                "POST",
                f"{self._auth_base}/mfa/challenge",
                headers=self._auth_headers(),
                json={"mfa_token": pending.mfa_token, "client_id": const.APP_CLIENT_ID, "challenge_type": "otp"},
            )
        received = time.time()
        resp = await self._fetch(
            "POST",
            f"{self._auth_base}/oauth/token",
            headers=self._auth_headers(),
            json={
                "grant_type": "http://auth0.com/oauth/grant-type/mfa-otp",
                "client_id": const.APP_CLIENT_ID,
                "mfa_token": pending.mfa_token,
                "otp": code,
            },
        )
        if resp.status in (400, 401, 403):
            error = self._auth0_error(resp.text) or ""
            if "expired" in error.lower():
                self._pending_mfa = None
                raise InvalidAuth("The 2FA challenge expired; start the login again")
            raise InvalidMfaCode(error or "Invalid code")
        if resp.status != 200:
            raise ApiError("verify_mfa", resp.status, resp.text)
        self._store_tokens(json.loads(resp.text), received)

    async def _verify_guardian(self, pending: _PendingMfa, code: str) -> None:
        """Auth0 Guardian hosted MFA ('Panasonic Digital Platform - MFA Standard')."""
        cfg = pending.guardian or {}
        service = cfg["serviceUrl"].rstrip("/")
        start = await self._fetch(
            "POST",
            f"{service}/api/start-flow",
            headers={"Authorization": f"Bearer {cfg['requestToken']}", "Accept": "application/json"},
            json={"state_transport": "polling"},
        )
        if start.status not in (200, 201, 204):
            raise ApiError("guardian_start", start.status, start.text)
        body = json.loads(start.text or "{}")
        tx_token = body.get("transactionToken") or body.get("transaction_token")
        if not tx_token:
            raise ApiError("guardian_start", start.status, "No transaction token")
        tx_headers = {"Authorization": f"Bearer {tx_token}", "Accept": "application/json"}

        verify = await self._fetch(
            "POST", f"{service}/api/verify-otp", headers=tx_headers, json={"type": "manual_input", "code": code}
        )
        if verify.status in (400, 401, 403):
            raise InvalidMfaCode("Invalid code")
        if verify.status not in (200, 201, 204):
            raise ApiError("guardian_verify", verify.status, verify.text)

        signature = None
        for _ in range(10):
            state = await self._fetch("POST", f"{service}/api/transaction-state", headers=tx_headers)
            if state.status not in (200, 201, 204):
                raise ApiError("guardian_state", state.status, state.text)
            data = json.loads(state.text or "{}")
            if data.get("state") == "accepted":
                signature = data.get("token")
                break
            if data.get("state") == "rejected":
                raise InvalidMfaCode("The 2FA login was rejected")
            await asyncio.sleep(1)
        if not signature:
            raise CannotConnect("Timed out waiting for 2FA confirmation")

        callback = await self._fetch(
            "POST",
            cfg["postActionURL"],
            data={"accepted": "true", "signature": signature},
            headers={"User-Agent": const.AUTH_BROWSER_USER_AGENT},
        )
        if callback.status not in (301, 302, 303) or not callback.location:
            raise ApiError("guardian_callback", callback.status, callback.text)
        code_value = await self._follow_for_code(callback.location, callback.url, pending.code_verifier)
        await self._exchange_code(code_value, pending.code_verifier)

    async def _refresh(self) -> None:
        received = time.time()
        resp = await self._fetch(
            "POST",
            f"{self._auth_base}/oauth/token",
            headers=self._auth_headers(),
            json={
                "scope": const.SCOPE,
                "client_id": const.APP_CLIENT_ID,
                "refresh_token": self._data.refresh_token,
                "grant_type": "refresh_token",
            },
        )
        if resp.status in (400, 401, 403):
            # invalid_grant: token revoked, rotated elsewhere, or expired
            raise InvalidAuth(self._auth0_error(resp.text) or "Refresh token rejected")
        if resp.status != 200:
            raise ApiError("refresh_token", resp.status, resp.text)
        self._store_tokens(json.loads(resp.text), received)

    async def _retrieve_client_id(self, retry: bool = True) -> None:
        resp = await self._fetch(
            "POST",
            f"{self._api_base}/auth/v2/login",
            headers=await self._api_headers(include_client_id=False),
            json={"language": 0},
        )
        if resp.status != 200 and retry and _error_code(resp.text) == const.ERROR_APP_VERSION_OUTDATED:
            await self._handle_outdated_app_version()
            return await self._retrieve_client_id(retry=False)
        if resp.status != 200:
            raise ApiError("client_id", resp.status, resp.text)
        client_id = json.loads(resp.text).get("clientId")
        if not client_id:
            raise ApiError("client_id", resp.status, resp.text)
        self._data.client_id = client_id
        self._notify()

    async def _handle_outdated_app_version(self) -> None:
        old = self.app_version
        new = await self._get_app_version(force=True)
        if new == old:
            _LOGGER.error(
                "Panasonic rejected app version %s as outdated and no newer version could be "
                "detected. Set the current Comfort Cloud app version in the integration options",
                old,
            )
        else:
            _LOGGER.info("Comfort Cloud app version updated %s -> %s", old, new)

    async def _ensure_session(self) -> None:
        async with self._lock:
            if self._auth_failures >= AUTH_FAILURES_BEFORE_LOGIN or not self._data.refresh_token:
                if self._auth_failures:
                    _LOGGER.info("Repeated auth errors; logging in again")
                await self._login()
                return
            if not self._data.access_valid:
                try:
                    await self._refresh()
                except InvalidAuth as err:
                    _LOGGER.info("Token refresh rejected (%s); logging in again", err)
                    await self._login()
                    return
            if not self._data.client_id:
                await self._retrieve_client_id()

    # ------------------------------------------------------------------- API
    async def _api(
        self,
        method: str,
        path: str,
        operation: str,
        *,
        json_data: Any = None,
        retry: bool = True,
    ) -> Any:
        await self._ensure_session()
        resp = await self._fetch(
            method, f"{self._api_base}{path}", headers=await self._api_headers(), json=json_data
        )
        if resp.status == 200:
            self._auth_failures = 0
            return json.loads(resp.text) if resp.text.strip() else {}

        code = _error_code(resp.text)
        _LOGGER.debug("%s failed: HTTP %s code %s body %s", operation, resp.status, code, resp.text[:500])

        if code == const.ERROR_AGREEMENT_REQUIRED:
            if path.startswith("/auth/v2/agreement"):
                raise AgreementRequired()
            if retry and await self._accept_agreements():
                return await self._api(method, path, operation, json_data=json_data, retry=False)
            raise AgreementRequired(await self._safe_pending_agreements())

        if retry and code == const.ERROR_APP_VERSION_OUTDATED:
            await self._handle_outdated_app_version()
            return await self._api(method, path, operation, json_data=json_data, retry=False)

        if resp.status in (401, 403) and code not in ("4300",):
            # Token/clientId rejected: refresh both and try once more
            self._auth_failures += 1
            if retry:
                self._data.expires_at = 0
                self._data.client_id = None
                return await self._api(method, path, operation, json_data=json_data, retry=False)

        raise ApiError(operation, resp.status, resp.text)

    # ---------------------------------------------------------- agreements
    async def async_get_pending_agreements(self) -> list[dict[str, Any]]:
        docs = await self._api(
            "GET", "/auth/v2/agreement/documents?language=0&includeContent=0", "agreement_documents", retry=False
        )
        accepted = await self._api("GET", "/auth/v2/agreement/status", "agreement_status", retry=False)
        accepted_versions = {
            int(item["type"]): item.get("version")
            for item in accepted.get("agreementList", [])
            if str(item.get("type", "")).isdigit()
        }
        pending = []
        for doc in docs.get("agreementList", []):
            try:
                doc_type = int(doc.get("type"))
            except (TypeError, ValueError):
                continue
            if doc_type not in const.AGREEMENT_TYPES_TO_ACCEPT or not doc.get("version"):
                continue
            if accepted_versions.get(doc_type) != doc["version"]:
                pending.append({"type": doc_type, "version": doc["version"]})
        return pending

    async def _safe_pending_agreements(self) -> list[dict[str, Any]]:
        try:
            return await self.async_get_pending_agreements()
        except Exception:  # noqa: BLE001 - only used to enrich an error message
            return []

    async def _accept_agreements(self) -> bool:
        if not self.auto_accept_agreements:
            return False
        pending = await self._safe_pending_agreements()
        if not pending:
            return False
        try:
            await self.async_accept_agreements(pending)
        except Exception as err:  # noqa: BLE001
            _LOGGER.warning("Could not accept updated Panasonic agreements %s: %s", pending, err)
            return False
        return True

    async def async_accept_agreements(self, agreements: list[dict[str, Any]]) -> None:
        _LOGGER.info("Accepting updated Panasonic agreements: %s", agreements)
        await self._api(
            "PUT",
            "/auth/v2/agreement/status",
            "accept_agreements",
            json_data={"agreementList": agreements},
            retry=False,
        )

    # ------------------------------------------------------------- devices
    async def async_get_devices(self) -> list[AcDevice]:
        """List the air conditioners on the account (Aquarea/HWS units are skipped)."""
        data = await self._api("GET", "/device/group", "get_groups")
        devices = []
        for group in data.get("groupList", []):
            group_name = group.get("groupName") or "Home"
            for entry in group.get("deviceList") or group.get("deviceIdList") or []:
                if AcDevice.is_air_conditioner(entry):
                    devices.append(AcDevice.from_group_entry(entry, group_name))
                elif entry:
                    _LOGGER.debug(
                        "Skipping non-AC device %s (type %s)", entry.get("deviceGuid"), entry.get("deviceType")
                    )
        return devices

    async def async_update_status(self, device: AcDevice) -> None:
        """Refresh a device. Uses the live endpoint, falling back to cloud-cached status."""
        guid = prepare_guid(device.guid)
        remaining_cached = self._cached_polls.get(device.guid, 0)
        if remaining_cached <= 0:
            try:
                status = await self._api("GET", f"/deviceStatus/{guid}", "get_status_live")
                device.load_status(status, const.StatusSource.LIVE)
                return
            except ApiError as err:
                _LOGGER.debug("Live status failed for %s, using cached status: %s", device.name, err)
                self._cached_polls[device.guid] = CACHED_POLLS_AFTER_LIVE_FAILURE
        else:
            self._cached_polls[device.guid] = remaining_cached - 1
        status = await self._api("GET", f"/deviceStatus/now/{guid}", "get_status_cached")
        device.load_status(status, const.StatusSource.CACHED)

    async def async_set_parameters(self, device: AcDevice, parameters: dict[str, Any]) -> None:
        """Send a control command, then merge it into the local state."""
        await self._api(
            "POST",
            "/deviceStatus/control",
            "set_device",
            json_data={"deviceGuid": device.guid, "parameters": parameters},
        )
        device.apply(parameters)
        # The device just answered; try live status again on the next poll
        self._cached_polls.pop(device.guid, None)

    async def async_get_energy_today(self, device: AcDevice, now: datetime) -> float | None:
        """Today's consumption in kWh, from the monthly history (as the app does)."""
        today = now.strftime("%Y%m%d")
        data = await self._api(
            "POST",
            "/deviceHistoryData",
            "get_energy",
            json_data={
                "deviceGuid": device.guid,
                "dataMode": 2,  # month view, one item per day
                "date": today,
                "osTimezone": utc_offset(now),
            },
        )
        for item in data.get("historyDataList", []):
            if item.get("dataTime") == today:
                value = item.get("consumption")
                if isinstance(value, (int, float)) and value >= 0:
                    return float(value)
                return None
        return None
