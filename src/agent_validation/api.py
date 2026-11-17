"""HTTP presentation layer."""

from __future__ import annotations

import os
import sys
from collections.abc import Sequence
from dataclasses import replace

import uvicorn
from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from .application import ServiceSettings, ValidationApplication, error_details
from .contracts import PartialValidationRequest, ValidationRequest, ValidationScope
from .errors import (
    InvalidRequestError,
    ProfileNotFoundError,
    ValidationServiceError,
)


def create_app(
    settings: ServiceSettings | None, application: ValidationApplication | None = None
) -> FastAPI:
    resolved_settings = settings or ServiceSettings.from_environment()
    validation_application = application or ValidationApplication(resolved_settings)
    app = FastAPI(title="Agent validation service", version="0.1.0")

    @app.exception_handler(RequestValidationError)
    async def malformed_request(request: Request, exc: RequestValidationError) -> JSONResponse:
        errors = [
            {"location": list(error["loc"]), "message": error["msg"]} for error in exc.errors()
        ]
        return JSONResponse(
            status_code=400,
            content={
                "error": {
                    "type": "invalid_request",
                    "message": "Malformed request",
                    "details": errors,
                }
            },
        )

    @app.exception_handler(ProfileNotFoundError)
    async def unknown_profile(request: Request, exc: ProfileNotFoundError) -> JSONResponse:
        return JSONResponse(status_code=404, content={"error": error_details(exc)})

    @app.exception_handler(InvalidRequestError)
    async def invalid_request(request: Request, exc: InvalidRequestError) -> JSONResponse:
        return JSONResponse(status_code=400, content={"error": error_details(exc)})

    @app.exception_handler(ValidationServiceError)
    async def service_failure(request: Request, exc: ValidationServiceError) -> JSONResponse:
        return JSONResponse(status_code=500, content={"error": error_details(exc)})

    @app.exception_handler(Exception)
    async def unexpected_failure(request: Request, exc: Exception) -> JSONResponse:
        return JSONResponse(status_code=500, content={"error": error_details(exc)})

    @app.get("/profiles/{profile}/configuration")
    def profile_configuration(profile: str) -> dict:
        return validation_application.profile_configuration(profile)

    @app.post("/validate")
    def validate(request: ValidationRequest) -> dict:
        return validation_application.validate(request, ValidationScope.FULL)

    @app.post("/validate_partial")
    def validate_partial(request: PartialValidationRequest) -> dict:
        return validation_application.validate(
            request,
            ValidationScope.PARTIAL,
            request.profile,
            request.configuration,
        )

    return app


app = create_app(None)


def run(arguments: Sequence[str] | None = None) -> None:
    values = list(sys.argv[1:] if arguments is None else arguments)
    port = int(os.environ.get("AGENT_VALIDATION_PORT", "8000"))
    profile = os.environ.get("AGENT_VALIDATION_PROFILE", "default")
    for value in values:
        if value.startswith("port="):
            port = int(value.split("=", 1)[1])
        elif value.startswith("profile="):
            profile = value.split("=", 1)[1]
        else:
            raise SystemExit(f"Unknown service argument: {value}")
    if not 1 <= port <= 65535:
        raise SystemExit("Service port must be between 1 and 65535")
    settings = replace(ServiceSettings.from_environment(), active_profile=profile)
    application = ValidationApplication(settings)
    application.profile_configuration(profile)
    uvicorn.run(
        create_app(settings, application),
        host=os.environ.get("AGENT_VALIDATION_HOST", "127.0.0.1"),
        port=port,
    )
