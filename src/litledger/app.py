"""Kept for the documented `litledger.app:create_app` import path; the server lives in litledger.server."""
from .server.app import create_app

__all__ = ["create_app"]
