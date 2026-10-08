"""The few standalone HTML pages the server renders itself (sign-in and OAuth outcomes), outside the web app."""
from __future__ import annotations

from html import escape

from fastapi.responses import HTMLResponse


def page(title: str, message: str, status: int = 400, link: tuple[str, str] | None = None) -> HTMLResponse:
    """A small centred card, optionally with one same-site link (href, label). All strings are escaped."""
    title, message = escape(title), escape(message)
    more = f"<p style='margin:12px 0 0'><a href='{escape(link[0])}'>{escape(link[1])}</a></p>" if link else ""
    html = (f"<!doctype html><meta charset=utf-8><meta name=viewport content='width=device-width'><title>{title}</title>"
            "<body style='font:15px system-ui;background:#e8ecf1;color:#172033;display:grid;place-items:center;min-height:90vh'>"
            f"<div style='background:#fbfcfd;border:1px solid #d6dce5;border-radius:12px;padding:22px 26px;max-width:460px'>"
            f"<h2 style='margin:0 0 8px;font-size:17px'>{title}</h2><p style='margin:0;color:#465166'>{message}</p>{more}</div>")
    return HTMLResponse(html, status_code=status)
