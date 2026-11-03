"""HTTP presentation layer."""

from __future__ import annotations

import os

import uvicorn
from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from .application import ServiceSettings, ValidationApplication, error_details
from .contracts import ValidationRequest, ValidationScope
from .errors import InvalidRequestError, ValidationServiceError


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

    @app.exception_handler(InvalidRequestError)
    async def invalid_request(request: Request, exc: InvalidRequestError) -> JSONResponse:
        return JSONResponse(status_code=400, content={"error": error_details(exc)})

    @app.exception_handler(ValidationServiceError)
    async def service_failure(request: Request, exc: ValidationServiceError) -> JSONResponse:
        return JSONResponse(status_code=500, content={"error": error_details(exc)})

    @app.exception_handler(Exception)
    async def unexpected_failure(request: Request, exc: Exception) -> JSONResponse:
        return JSONResponse(status_code=500, content={"error": error_details(exc)})

    @app.post("/validate")
    def validate(request: ValidationRequest) -> dict:
        return validation_application.validate(request, ValidationScope.FULL)

    @app.post("/validate_partial")
    def validate_partial(request: ValidationRequest) -> dict:
        return validation_application.validate(request, ValidationScope.PARTIAL)

    return app


app = create_app(None)


def run() -> None:
    uvicorn.run(
        "agent_validation.api:app",
        host=os.environ.get("AGENT_VALIDATION_HOST", "127.0.0.1"),
        port=int(os.environ.get("AGENT_VALIDATION_PORT", "8000")),
    )
