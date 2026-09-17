"""API v1 router aggregation."""

from __future__ import annotations

from fastapi import APIRouter

from app.api.v1 import wallets

api_router = APIRouter(prefix="/api/v1")
api_router.include_router(wallets.router)
