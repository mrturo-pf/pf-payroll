"""Healthcheck routes."""

from fastapi import APIRouter

router = APIRouter()


@router.get("/health")
async def health() -> dict[str, str]:
    """Handle health."""
    return {"status": "ok"}
