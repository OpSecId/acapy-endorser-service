"""This module sets up the main API routing for the Aries Endorser Service."""

from fastapi import APIRouter

from api.endpoints.routes import connections, endorse, reports, admin, allow

endorser_router = APIRouter()
endorser_router.include_router(admin.router, prefix="/admin", tags=["admin"])
endorser_router.include_router(allow.router, prefix="/allow", tags=["allow"])
endorser_router.include_router(connections.router, prefix="/connections", tags=["connections"])
endorser_router.include_router(endorse.router, prefix="/endorse", tags=["endorse"])
endorser_router.include_router(reports.router, prefix="/reports", tags=["reports"])
