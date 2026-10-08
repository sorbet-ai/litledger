"""Signing in from a browser (docs/ACCESS.md).

People sign in with their email and password, or with Google or GitHub once an admin has set those up. They create
their own accounts (name, email, password, or Google/GitHub) when the sign-up policy lets them, or accept an invite.
The root admin signs in with the admin token from the server (`cat /data/tokens/admin`). A forgotten password is reset
with an emailed link, or with a link an admin makes (Admin → People, or `litledger reset-password EMAIL`).

The routes answer the web app with JSON, except the Google/GitHub round trip and the email confirmation link, which
redirect. A signed-in browser holds an HttpOnly session cookie. JSON posts need the X-Litledger-CSRF header, like every
cookie write, so another site can't sign a browser in or out."""
from __future__ import annotations

import base64
import hmac
import json
import logging
import secrets
import smtplib
from email.message import EmailMessage
from urllib.parse import urlencode

import httpx
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse, RedirectResponse

from . import accounts, auth
from .auth import Denied, Limiter, TTLStore
from .ledger import Ledger
from .oauth import base_url
from .pages import page
from .server.rest import client_ip, ctx_of, guard, read_body, run

log = logging.getLogger("litledger.signin")
COOKIE = "litledger_session"
FLOW_COOKIE = "litledger_flow"  # ties a Google/GitHub round trip to the browser that started it
STATE_TTL = 600  # a Google/GitHub round trip
PROVIDERS = {
    "github": {"authorize": "https://github.com/login/oauth/authorize", "token": "https://github.com/login/oauth/access_token",
               "scope": "read:user user:email"},
    "google": {"authorize": "https://accounts.google.com/o/oauth2/v2/auth", "token": "https://oauth2.googleapis.com/token",
               "scope": "openid email profile"},
}


def safe_next(value: str | None) -> str:
    """Only same-site relative paths, never //host or scheme URLs."""
    value = value or "/"
    return value if value.startswith("/") and not value.startswith("//") and "\\" not in value else "/"


def need_csrf(request: Request) -> None:
    if request.headers.get("x-litledger-csrf") != "1":
        raise HTTPException(403, "Browser requests need the X-Litledger-CSRF: 1 header.")


# ------------------------------------------------------------------------------------------------ email
def can_mail(lg: Ledger) -> bool:
    return bool(lg.settings.get("SMTP_HOST"))


def send_mail(lg: Ledger, to: str, subject: str, body: str) -> bool:
    s = lg.settings
    host = s.get("SMTP_HOST")
    if not host:
        return False
    port = s.number("SMTP_PORT")
    msg = EmailMessage()
    msg["From"] = s.get("SMTP_FROM") or s.get("SMTP_USER") or f"litledger@{host}"
    msg["To"] = to
    msg["Subject"] = subject
    msg.set_content(body)
    server = smtplib.SMTP_SSL(host, port, timeout=15) if port == 465 else smtplib.SMTP(host, port, timeout=15)
    with server:
        if port != 465:
            server.starttls()
        if s.get("SMTP_USER"):
            server.login(s.get("SMTP_USER"), s.get("SMTP_PASSWORD") or "")
        server.send_message(msg)
    return True


def try_mail(lg: Ledger, to: str, subject: str, body: str) -> bool:
    """Send if email is set up; a failure is logged, never shown (it must not reveal whether an account exists)."""
    try:
        return send_mail(lg, to, subject, body)
    except Exception as exc:
        log.warning("could not send email to %s: %s", to, exc)
        return False


def link(lg: Ledger, request: Request, path: str, token: str) -> str:
    return f"{base_url(lg, request)}{path}?{urlencode({'t': token})}"


# ------------------------------------------------------------------------------------------------ sessions
def set_session(lg: Ledger, request: Request, response, raw: str) -> None:
    secure = base_url(lg, request).startswith("https://")
    response.set_cookie(COOKIE, raw, max_age=auth.SESSION_DAYS * 86400, httponly=True, samesite="lax", secure=secure, path="/")


def methods(lg: Ledger) -> dict:
    s = lg.settings
    policy = (s.get("SIGNUP") or "anyone").lower()
    return {"signup": policy, "password_signup": accounts.password_signup(s, can_mail(lg)),
            "domains": accounts.signup_domains(s) if policy == "domains" else [],
            "github": bool(s.get("GITHUB_CLIENT_ID") and s.get("GITHUB_CLIENT_SECRET")),
            "google": bool(s.get("GOOGLE_CLIENT_ID") and s.get("GOOGLE_CLIENT_SECRET")), "email": can_mail(lg)}


def _identity(lg: Ledger, provider: str, code: str, redirect_uri: str) -> tuple[str, str | None, str | None]:
    """(subject, verified email, login) from GitHub or Google, or raises."""
    s = lg.settings
    data = {"client_id": s.get(f"{provider.upper()}_CLIENT_ID"), "client_secret": s.get(f"{provider.upper()}_CLIENT_SECRET"),
            "code": code, "redirect_uri": redirect_uri}
    with httpx.Client(timeout=15, headers={"Accept": "application/json", "User-Agent": "litledger"}) as c:
        if provider == "github":
            tok = c.post(PROVIDERS["github"]["token"], data=data).json().get("access_token")
            if not tok:
                raise ValueError("GitHub did not return a token")
            h = {"Authorization": f"Bearer {tok}"}
            user = c.get("https://api.github.com/user", headers=h).json()
            emails = c.get("https://api.github.com/user/emails", headers=h).json()
            verified = [e for e in emails if isinstance(e, dict) and e.get("verified")] if isinstance(emails, list) else []
            primary = next((e["email"] for e in verified if e.get("primary")), verified[0]["email"] if verified else None)
            return str(user["id"]), (primary or "").lower() or None, user.get("login")
        r = c.post(PROVIDERS["google"]["token"], data={**data, "grant_type": "authorization_code"}).json()
        # The id_token came straight from Google over TLS, so its claims can be read without checking the signature.
        payload = r["id_token"].split(".")[1]
        claims = json.loads(base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4)))
        if claims.get("aud") != s.get("GOOGLE_CLIENT_ID") or claims.get("iss") not in ("https://accounts.google.com", "accounts.google.com"):
            raise ValueError("unexpected Google token")
        email = claims.get("email") if claims.get("email_verified") else None
        return str(claims["sub"]), (email or "").lower() or None, claims.get("name")


# ------------------------------------------------------------------------------------------------ routes
def register_signin(app: FastAPI, lg: Ledger) -> None:
    """Every route calls one action in accounts.py with the request's context; the action writes its own audit rows."""
    states = TTLStore(STATE_TTL, cap=1000)  # Google/GitHub round trips in flight (a flood evicts the oldest)
    login_limit = Limiter(10, 900)  # wrong passwords, per email and per address
    mail_limit = Limiter(5, 900)  # reset emails
    link_limit = Limiter(30, 900)  # token sign-in, reset and invite pages, per address
    signup_limit = Limiter(10, 3600)  # new accounts per address
    start_limit = Limiter(30, 600)  # Google/GitHub round trips started, per address

    def with_session(request: Request, raw: str, response):
        set_session(lg, request, response, raw)
        return response

    def session_json(request: Request, raw: str, extra: dict | None = None) -> JSONResponse:
        row = auth.lookup(lg.db, raw, "session")
        return with_session(request, raw, JSONResponse({"name": row["name"], **(extra or {})}))

    async def body_of(request: Request, limit: Limiter | None = None) -> dict:
        need_csrf(request)
        if limit and not limit.ok("ip:" + (client_ip(request) or "?")):
            raise HTTPException(429, "Too many attempts. Wait a few minutes.")
        return await read_body(request)

    @app.get("/auth/methods")
    async def auth_methods():
        return await run(methods, lg)

    @app.post("/auth/signup")
    async def signup(request: Request):
        """A new member with a name, email and password. With allowed domains the account is made from an emailed link."""
        body = await body_of(request)
        email = str(body.get("email") or "").strip().lower()
        out = await guard(lambda: accounts.signup(lg.db, lg.settings, str(body.get("name") or ""), email,
                                                  str(body.get("password") or ""), can_mail(lg), ctx_of(request), signup_limit))
        if out.link:
            text = (f"Finish creating your litledger account:\n\n{link(lg, request, '/auth/signup/confirm', out.link)}\n\n"
                    f"The link expires in {accounts.EMAIL_HOURS} hours. If you didn't ask for this, ignore this email.")
            await run(try_mail, lg, email, "Confirm your litledger account", text)
            return {"confirm": email}
        if out.confirm:  # confirm the address, so it can link a Google/GitHub account later
            text = (f"Confirm your litledger email (open the link in the browser where you signed up):\n\n"
                    f"{link(lg, request, '/auth/email/confirm', out.confirm)}\n\n"
                    "If you didn't create this account, someone used your address: reset its password from the sign-in "
                    "page to take it over and sign them out.")
            await run(try_mail, lg, email, "Confirm your litledger email", text)
        return await run(session_json, request, out.session)

    @app.get("/auth/signup/confirm")
    async def signup_confirm(request: Request, t: str = ""):
        try:
            raw = await run(accounts.finish_signup, lg.db, t, ctx_of(request))
        except ValueError as exc:
            return page("Can't create the account", str(exc))
        if not raw:
            return page("This link has expired", "Create your account again from the sign-in page.")
        return with_session(request, raw, RedirectResponse("/", status_code=303))

    @app.post("/auth/token")
    async def token_login(request: Request):
        """The root admin signs in with the admin token. The browser gets a session; the token isn't kept."""
        body = await body_of(request)
        raw = await guard(lambda: accounts.sign_in_token(lg.db, str(body.get("token") or ""), ctx_of(request), link_limit))
        return await run(session_json, request, raw)

    @app.post("/auth/password")
    async def password_login(request: Request):
        body = await body_of(request)
        raw = await guard(lambda: accounts.sign_in_password(lg.db, str(body.get("email") or ""), str(body.get("password") or ""),
                                                            ctx_of(request), login_limit))
        return await run(session_json, request, raw)

    @app.post("/auth/logout")
    async def logout(request: Request):
        need_csrf(request)
        await run(accounts.logout, lg.db, request.cookies.get(COOKIE), ctx_of(request))
        resp = JSONResponse({"ok": True})
        resp.delete_cookie(COOKIE, path="/")
        return resp

    # -- forgotten passwords -----------------------------------------------------------------------------
    @app.post("/auth/forgot")
    async def forgot(request: Request):
        """The same answer whether or not the email has an account (the audit log records which)."""
        body = await body_of(request)
        email = str(body.get("email") or "").strip().lower()
        if not email:
            raise HTTPException(400, "Enter your email.")
        raw = await guard(lambda: accounts.request_reset(lg.db, email, can_mail(lg), ctx_of(request), mail_limit))
        if not can_mail(lg):
            return {"sent": False, "command": f"litledger reset-password {email}"}
        if raw:
            text = (f"Someone asked to reset the litledger password for {email}.\n\nSet a new one here:\n"
                    f"{link(lg, request, '/reset', raw)}\n\nThe link works once and expires in {accounts.RESET_MINUTES} "
                    "minutes. If it wasn't you, ignore this email.")
            await run(try_mail, lg, email, "Reset your litledger password", text)
        return {"sent": True}

    @app.get("/auth/reset")
    async def reset_check(request: Request, t: str = ""):
        if not link_limit.ok("ip:" + (client_ip(request) or "?")):
            raise HTTPException(429, "Too many attempts. Wait a few minutes.")
        info = await run(accounts.reset_info, lg.db, t)
        if not info:
            raise HTTPException(404, "This link has expired or was already used.")
        return info

    @app.post("/auth/reset")
    async def reset(request: Request):
        body = await body_of(request, link_limit)
        out = await guard(lambda: accounts.reset_password(lg.db, str(body.get("token") or ""), str(body.get("password") or ""),
                                                          ctx_of(request)))
        # the web app shows the notice once, after a reset that took the account over
        return await run(session_json, request, out.session, {"notice": accounts.RECLAIMED} if out.reclaimed else None)

    # -- invitations ---------------------------------------------------------------------------------------
    @app.get("/auth/invite")
    async def invite_check(request: Request, t: str = ""):
        if not link_limit.ok("ip:" + (client_ip(request) or "?")):
            raise HTTPException(429, "Too many attempts. Wait a few minutes.")
        info = await run(accounts.invite_info, lg.db, t, ctx_of(request))
        if not info:
            raise HTTPException(404, "This invite has expired or was already used. Ask for a new one.")
        return info

    @app.post("/auth/invite")
    async def invite_accept(request: Request):
        body = await body_of(request, link_limit)
        raw = await guard(lambda: accounts.accept_invite(lg.db, str(body.get("token") or ""), str(body.get("name") or ""),
                                                         str(body.get("password") or ""), ctx_of(request)))
        return await run(session_json, request, raw)

    # -- a new email address -------------------------------------------------------------------------------
    @app.get("/auth/email/confirm")
    async def email_confirm(request: Request, t: str = ""):
        raw = request.cookies.get(COOKIE)
        me = await run(auth.verify, lg.db, raw) if raw else None
        try:
            pid = await run(accounts.confirm_email, lg.db, t, ctx_of(request, me), me.principal_id if me else None)
        except PermissionError as exc:
            return page("Sign in first", str(exc), 403, ("/", "Sign in"))
        except ValueError as exc:
            return page("Can't change your email", str(exc))
        if not pid:
            return page("This link has expired", "Change your email again from Settings → Profile.")
        return RedirectResponse("/settings/profile?email=confirmed", status_code=303)

    # -- Google and GitHub -----------------------------------------------------------------------------------
    @app.get("/auth/{provider}/start")
    async def provider_start(provider: str, request: Request, next: str = "/", link: int = 0, invite: str = ""):
        if provider not in PROVIDERS:
            raise HTTPException(404)
        if not start_limit.ok("ip:" + (client_ip(request) or "?")):
            return RedirectResponse(f"/?error=limited&provider={provider}", status_code=303)
        cid = lg.settings.get(f"{provider.upper()}_CLIENT_ID")
        if not cid or not lg.settings.get(f"{provider.upper()}_CLIENT_SECRET"):
            return RedirectResponse(f"/?error=not_set_up&provider={provider}", status_code=303)
        linking = None
        if link:
            raw = request.cookies.get(COOKIE)
            actor = await run(auth.verify, lg.db, raw) if raw else None
            if not actor or actor.kind != "human":
                return RedirectResponse("/", status_code=303)
            linking = actor.principal_id
        if invite and not await run(accounts.invite_info, lg.db, invite, ctx_of(request)):
            return RedirectResponse("/?error=invite", status_code=303)
        nonce = secrets.token_urlsafe(16)
        state = states.put({"provider": provider, "next": safe_next(next), "link": linking, "invite": invite or None,
                            "nonce": nonce})
        params = {"client_id": cid, "redirect_uri": f"{base_url(lg, request)}/auth/{provider}/callback", "state": state,
                  "scope": PROVIDERS[provider]["scope"]}
        if provider == "google":
            params.update(response_type="code", prompt="select_account")
        resp = RedirectResponse(f"{PROVIDERS[provider]['authorize']}?{urlencode(params)}", status_code=302)
        resp.set_cookie(FLOW_COOKIE, nonce, max_age=STATE_TTL, httponly=True, samesite="lax", path="/auth/",
                        secure=base_url(lg, request).startswith("https://"))
        return resp

    @app.get("/auth/{provider}/callback")
    async def provider_callback(provider: str, request: Request, code: str = "", state: str = "", error: str = ""):
        st = states.pop(state)
        fail = lambda why: RedirectResponse(f"/?error={why}&provider={provider}", status_code=303)  # noqa: E731
        # The state must come back to the browser that started: a callback URL someone else began is refused (and
        # recorded), so nobody can sign your browser into their account.
        if not st or st["provider"] != provider or not hmac.compare_digest(request.cookies.get(FLOW_COOKIE, ""), st["nonce"]):
            await run(accounts.flow_mismatch, lg.db, provider, ctx_of(request))
            return fail("expired")
        if error or not code:
            return fail("cancelled")
        try:
            subject, email, login = await run(_identity, lg, provider, code, f"{base_url(lg, request)}/auth/{provider}/callback")
        except Exception as exc:
            log.warning("%s sign-in failed: %s", provider, exc)
            return fail("failed")
        ctx = ctx_of(request)
        if st["link"]:
            try:
                await run(accounts.link_identity, lg.db, st["link"], provider, subject, email, login, ctx)
            except ValueError:
                return RedirectResponse("/settings/profile?error=taken", status_code=303)
            return RedirectResponse("/settings/profile?linked=" + provider, status_code=303)
        try:
            raw = await run(accounts.sign_in_identity, lg.db, lg.settings, provider, subject, email, login, st["invite"], ctx)
        except Denied as exc:
            return fail(exc.code)
        return with_session(request, raw, RedirectResponse(safe_next(st["next"]), status_code=303))
