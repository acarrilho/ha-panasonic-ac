"""A fake Panasonic Auth0 + Comfort Cloud server for tests.

Reproduces the parts of the real protocol the client depends on: PKCE checks,
the CSRF login form, classic and Guardian 2FA, refresh-token rotation and
revocation, request signing, the app-version gate (4106), the terms gate (4103),
and the device status/control/history endpoints.
"""

from __future__ import annotations

import base64
import copy
import hashlib
import json
import secrets
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from aiohttp import web

from custom_components.panasonic_ac.api import const
from custom_components.panasonic_ac.api.client import api_key

RAC_GUID = "CS-Z25XKEW+1234567890"
CAC_GUID = "S-71PF1E5B/9876543210"
AQUAREA_GUID = "B123456789"

OTP = "123456"


def rac_status() -> dict[str, Any]:
    return {
        "timestamp": 1729250000000,
        "permission": 3,
        "summerHouse": 2,
        "iAutoX": False,
        "nanoe": True,
        "nanoeStandAlone": False,
        "autoMode": True,
        "heatMode": True,
        "fanMode": True,
        "dryMode": True,
        "coolMode": True,
        "ecoNavi": False,
        "powerfulMode": True,
        "quietMode": True,
        "airSwingLR": True,
        "autoSwingUD": False,
        "ecoFunction": 0,
        "temperatureUnit": 0,
        "insideCleaning": True,
        "powerConsumptionFlg": True,
        "parameters": {
            "operate": 1,
            "operationMode": 3,
            "temperatureSet": 21.5,
            "fanSpeed": 0,
            "fanAutoMode": 1,
            "airSwingLR": 2,
            "airSwingUD": 2,
            "ecoMode": 0,
            "ecoNavi": 0,
            "nanoe": 1,
            "iAuto": 0,
            "airDirection": 0,
            "insideTemperature": 19,
            "outTemperature": 9,
            "insideCleaning": 0,
            "ecoFunctionData": 0,
        },
    }


def cac_status() -> dict[str, Any]:
    return {
        "timestamp": 1729250000000,
        "permission": 3,
        "autoMode": True,
        "heatMode": True,
        "fanMode": True,
        "dryMode": True,
        "coolMode": True,
        "powerfulMode": True,
        "quietMode": True,
        "airSwingLR": True,
        "nanoe": False,
        "fanSpeedMode": 3,
        "powerConsumptionFlg": False,
        "parameters": {
            "operate": 0,
            "operationMode": 2,
            "temperatureSet": 24,
            "fanSpeed": 3,
            "fanAutoMode": 2,
            "airSwingLR": 2,
            "airSwingUD": 0,
            "ecoMode": 0,
            "nanoe": 0,
            "insideTemperature": 126,
            "outTemperature": 126,
        },
    }


@dataclass
class FakePanasonic:
    username: str = "me@example.com"
    password: str = "secret"
    mfa: str | None = None  # None, "classic" or "guardian"
    required_app_version: str = const.APP_VERSION_FALLBACK
    play_store_version: str | None = None
    agreement_pending: bool = False
    live_status_fails: bool = False
    access_token_lifetime: int = 3600
    statuses: dict[str, dict] = field(
        default_factory=lambda: {RAC_GUID: rac_status(), CAC_GUID: cac_status()}
    )
    base_url: str = ""
    # observed traffic
    calls: list[str] = field(default_factory=list)
    controls: list[dict] = field(default_factory=list)
    logins: int = 0
    refreshes: int = 0
    accepted_agreements: list[dict] = field(default_factory=list)

    def __post_init__(self) -> None:
        self._codes: dict[str, str] = {}  # code -> code_challenge
        self._pending_states: dict[str, str] = {}  # state -> code_challenge
        self._mfa_tokens: dict[str, str] = {}
        self._guardian: dict[str, str] = {}
        self.access_tokens: set[str] = set()
        self.refresh_tokens: set[str] = set()
        self.client_ids: set[str] = set()

    # ------------------------------------------------------------- helpers
    def revoke_refresh_tokens(self) -> None:
        self.refresh_tokens.clear()

    def revoke_access_tokens(self) -> None:
        self.access_tokens.clear()

    def _issue_tokens(self) -> dict[str, Any]:
        access = secrets.token_hex(16)
        refresh = secrets.token_hex(16)
        self.access_tokens.add(access)
        self.refresh_tokens.add(refresh)
        return {"access_token": access, "refresh_token": refresh, "expires_in": self.access_token_lifetime}

    def _redirect_with_code(self, state: str) -> web.Response:
        challenge = self._pending_states.pop(state)
        code = secrets.token_hex(8)
        self._codes[code] = challenge
        raise web.HTTPFound(f"{const.REDIRECT_URI}?code={code}&state={state}")

    @staticmethod
    def _error(status: int, code: int, message: str = "error") -> web.Response:
        return web.json_response({"code": code, "message": message}, status=status)

    def app(self) -> web.Application:
        app = web.Application()
        r = app.router
        r.add_get("/authorize", self.authorize)
        r.add_get("/login", self.login_page)
        r.add_post("/usernamepassword/login", self.usernamepassword_login)
        r.add_post("/login/callback", self.login_callback)
        r.add_get("/authorize/resume", self.authorize_resume)
        r.add_get("/mfa-page", self.mfa_page)
        r.add_post("/mfa/challenge", self.mfa_challenge)
        r.add_post("/oauth/token", self.oauth_token)
        r.add_post("/guardian/api/start-flow", self.guardian_start)
        r.add_post("/guardian/api/verify-otp", self.guardian_verify)
        r.add_post("/guardian/api/transaction-state", self.guardian_state)
        r.add_post("/guardian/continue", self.guardian_continue)
        r.add_get("/store/apps/details", self.play_store)
        r.add_post("/auth/v2/login", self.acc_login)
        r.add_get("/device/group", self.device_group)
        r.add_get("/deviceStatus/now/{guid}", self.device_status_cached)
        r.add_get("/deviceStatus/{guid}", self.device_status_live)
        r.add_post("/deviceStatus/control", self.device_control)
        r.add_post("/deviceHistoryData", self.history)
        r.add_get("/auth/v2/agreement/documents", self.agreement_documents)
        r.add_get("/auth/v2/agreement/status", self.agreement_status)
        r.add_put("/auth/v2/agreement/status", self.agreement_accept)
        return app

    # ---------------------------------------------------------------- auth0
    async def authorize(self, request: web.Request) -> web.Response:
        q = request.query
        assert q["client_id"] == const.APP_CLIENT_ID
        assert q["code_challenge_method"] == "S256"
        self._pending_states[q["state"]] = q["code_challenge"]
        raise web.HTTPFound(f"/login?state={q['state']}&client={q['client_id']}")

    async def login_page(self, request: web.Request) -> web.Response:
        resp = web.Response(text="<html>login</html>", content_type="text/html")
        resp.set_cookie("_csrf", "csrf-token-value")
        return resp

    async def usernamepassword_login(self, request: web.Request) -> web.Response:
        self.calls.append("login")
        body = await request.json()
        assert body["_csrf"] == "csrf-token-value", "csrf missing"
        assert body["state"] in self._pending_states
        if body["username"] != self.username or body["password"] != self.password:
            return web.json_response(
                {"name": "ValidationError", "code": "invalid_user_password", "description": "Wrong email or password."},
                status=401,
            )
        state = body["state"]
        if self.mfa == "classic":
            token = secrets.token_hex(8)
            self._mfa_tokens[token] = state
            return web.Response(
                text=f'<form method="post"><input type="hidden" name="mfa_token" value="{token}"/></form>',
                content_type="text/html",
            )
        return web.Response(
            text=(
                '<form method="post" name="hiddenform" action="/login/callback">'
                '<input type="hidden" name="wa" value="wsignin1.0">'
                f'<input type="hidden" name="wresult" value="{state}">'
                '<input type="hidden" name="wctx" value="{&#34;strategy&#34;:&#34;auth0&#34;}">'
                "</form>"
            ),
            content_type="text/html",
        )

    async def login_callback(self, request: web.Request) -> web.Response:
        form = await request.post()
        state = form["wresult"]
        if self.mfa == "guardian":
            raise web.HTTPFound(f"/mfa-page?state={state}")
        raise web.HTTPFound(f"/authorize/resume?state={state}")

    async def mfa_page(self, request: web.Request) -> web.Response:
        state = request.query["state"]
        request_token = secrets.token_hex(8)
        self._guardian[request_token] = state
        html = (
            "<script>window.__g_config = {\n"
            f'  postActionURL: "{self.base_url}\\/guardian\\/continue?state={state}",\n'
            f'  serviceUrl: "{self.base_url}/guardian", // comment\n'
            f'  requestToken: "{request_token}",\n'
            '  stateCheckingMechanism: "polling"\n};</script>'
        )
        return web.Response(text=html, content_type="text/html")

    async def mfa_challenge(self, request: web.Request) -> web.Response:
        return web.json_response({"challenge_type": "otp"})

    async def authorize_resume(self, request: web.Request) -> web.Response:
        return self._redirect_with_code(request.query["state"])

    async def oauth_token(self, request: web.Request) -> web.Response:
        body = await request.json()
        grant = body["grant_type"]
        if grant == "authorization_code":
            challenge = self._codes.pop(body["code"], None)
            expected = (
                base64.urlsafe_b64encode(hashlib.sha256(body["code_verifier"].encode()).digest())
                .decode()
                .rstrip("=")
            )
            if challenge is None or challenge != expected:
                return web.json_response({"error": "invalid_grant", "error_description": "Invalid code"}, status=403)
            self.logins += 1
            return web.json_response(self._issue_tokens())
        if grant == "refresh_token":
            token = body["refresh_token"]
            if token not in self.refresh_tokens:
                return web.json_response(
                    {"error": "invalid_grant", "error_description": "Unknown or invalid refresh token."}, status=403
                )
            self.refresh_tokens.discard(token)  # rotation
            self.refreshes += 1
            return web.json_response(self._issue_tokens())
        if grant == "http://auth0.com/oauth/grant-type/mfa-otp":
            if body["mfa_token"] not in self._mfa_tokens:
                return web.json_response({"error": "expired_token", "error_description": "mfa_token is expired"}, status=403)
            if body["otp"] != OTP:
                return web.json_response({"error": "invalid_grant", "error_description": "Invalid otp_code."}, status=403)
            del self._mfa_tokens[body["mfa_token"]]
            self.logins += 1
            return web.json_response(self._issue_tokens())
        return web.json_response({"error": "unsupported_grant_type"}, status=400)

    async def guardian_start(self, request: web.Request) -> web.Response:
        token = request.headers["Authorization"].removeprefix("Bearer ")
        assert token in self._guardian
        tx = f"tx-{token}"
        return web.json_response({"transactionToken": tx})

    async def guardian_verify(self, request: web.Request) -> web.Response:
        body = await request.json()
        if body["code"] != OTP:
            return web.json_response({"error": "invalid_otp"}, status=403)
        tx = request.headers["Authorization"].removeprefix("Bearer ")
        self._guardian[f"ok-{tx}"] = "1"
        return web.Response(status=204)

    async def guardian_state(self, request: web.Request) -> web.Response:
        tx = request.headers["Authorization"].removeprefix("Bearer ")
        if f"ok-{tx}" in self._guardian:
            return web.json_response({"state": "accepted", "token": f"sig-{tx}"})
        return web.json_response({"state": "pending"})

    async def guardian_continue(self, request: web.Request) -> web.Response:
        form = await request.post()
        assert form["accepted"] == "true" and form["signature"].startswith("sig-")
        raise web.HTTPFound(f"/authorize/resume?state={request.query['state']}")

    async def play_store(self, request: web.Request) -> web.Response:
        version = self.play_store_version or "0.0.1"
        return web.Response(text=f'junk ["1.2.3"] more [[["{version}"]],[[[33]]]]', content_type="text/html")

    # ------------------------------------------------------------ comfort cloud
    def _check_api(self, request: web.Request, need_client_id: bool = True) -> web.Response | None:
        h = request.headers
        if h.get("X-APP-VERSION") != self.required_app_version:
            return self._error(401, 4106, "New version app has been published")
        token = h.get("X-User-Authorization-V2", "").removeprefix("Bearer ")
        if token not in self.access_tokens:
            return self._error(401, 4100, "Token expires")
        if h.get("X-CFC-API-KEY") != api_key(h["X-APP-TIMESTAMP"], token):
            return self._error(401, 4101, "Bad signature")
        if need_client_id and h.get("X-Client-Id") not in self.client_ids:
            return self._error(401, 4102, "Unknown client")
        if self.agreement_pending and not request.path.startswith("/auth/"):
            return self._error(403, 4103, "Terms updated")
        return None

    async def acc_login(self, request: web.Request) -> web.Response:
        if err := self._check_api(request, need_client_id=False):
            return err
        client_id = secrets.token_hex(8)
        self.client_ids.add(client_id)
        return web.json_response({"clientId": client_id, "result": 0})

    async def device_group(self, request: web.Request) -> web.Response:
        self.calls.append("group")
        if err := self._check_api(request):
            return err
        devices = []
        for guid, status in self.statuses.items():
            devices.append(
                {
                    "deviceGuid": guid,
                    "deviceType": "5" if guid == CAC_GUID else "3",
                    "deviceName": "Ducted" if guid == CAC_GUID else "Living room",
                    "deviceModuleNumber": "CS-Z25XKEW",
                    "parameters": copy.deepcopy(status["parameters"]),
                }
            )
        devices.append({"deviceGuid": AQUAREA_GUID, "deviceType": "2", "deviceName": "Heat pump"})
        return web.json_response({"groupCount": 1, "groupList": [{"groupId": 1, "groupName": "Casa", "deviceList": devices}]})

    def _guid(self, request: web.Request) -> str:
        wanted = request.match_info["guid"]
        for guid in self.statuses:
            if guid.replace("/", "f") == wanted:
                return guid
        raise web.HTTPNotFound()

    async def device_status_live(self, request: web.Request) -> web.Response:
        self.calls.append("status_live")
        if err := self._check_api(request):
            return err
        if self.live_status_fails:
            return self._error(500, 5005, "Device not responding")
        return web.json_response(self.statuses[self._guid(request)])

    async def device_status_cached(self, request: web.Request) -> web.Response:
        self.calls.append("status_cached")
        if err := self._check_api(request):
            return err
        return web.json_response(self.statuses[self._guid(request)])

    async def device_control(self, request: web.Request) -> web.Response:
        if err := self._check_api(request):
            return err
        body = await request.json()
        self.controls.append(body)
        self.statuses[body["deviceGuid"]]["parameters"].update(body["parameters"])
        return web.json_response({"result": 0})

    async def history(self, request: web.Request) -> web.Response:
        if err := self._check_api(request):
            return err
        body = await request.json()
        assert body["dataMode"] == 2
        assert body["osTimezone"][0] in "+-" and len(body["osTimezone"]) == 6
        day = datetime.strptime(body["date"], "%Y%m%d")
        items = [{"dataNumber": 0, "dataTime": day.strftime("%Y%m01"), "consumption": 0.4}]
        items.append({"dataNumber": 1, "dataTime": body["date"], "consumption": 3.2})
        return web.json_response({"energyConsumption": 3.6, "historyDataList": items})

    async def agreement_documents(self, request: web.Request) -> web.Response:
        if err := self._check_api(request):
            return err
        return web.json_response(
            {"agreementList": [{"type": str(t), "version": "2.0"} for t in (1, 2, 3, 4)]}
        )

    async def agreement_status(self, request: web.Request) -> web.Response:
        if err := self._check_api(request):
            return err
        version = "1.0" if self.agreement_pending else "2.0"
        return web.json_response({"agreementList": [{"type": t, "version": version} for t in (1, 2, 4)]})

    async def agreement_accept(self, request: web.Request) -> web.Response:
        if err := self._check_api(request):
            return err
        body = await request.json()
        if any(item["type"] == 3 for item in body["agreementList"]):
            return self._error(400, 4000, "Bad request")
        self.accepted_agreements.extend(body["agreementList"])
        self.agreement_pending = False
        return web.json_response({"result": 0})


async def start(fake: FakePanasonic) -> tuple[web.AppRunner, str]:
    runner = web.AppRunner(fake.app())
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0)
    await site.start()
    port = site._server.sockets[0].getsockname()[1]  # noqa: SLF001
    fake.base_url = f"http://127.0.0.1:{port}"
    return runner, fake.base_url


def json_dumps(data: Any) -> str:
    return json.dumps(data)
