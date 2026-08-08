from starlette.requests import Request

from app import create_error_response


def _request() -> Request:
    scope = {
        "type": "http",
        "method": "POST",
        "path": "/api/v1/agent/runs",
        "headers": [],
        "query_string": b"",
    }
    return Request(scope)


def test_create_error_response_serializes_validation_exceptions() -> None:
    response = create_error_response(
        request=_request(),
        status_code=422,
        error_status="invalid_model_output",
        error_code="MODEL_SCHEMA_VALIDATION_FAILED",
        message="Schema validation failed.",
        validation_errors=[
            {
                "loc": ["arguments"],
                "msg": "Value error",
                "ctx": {"error": ValueError("boom")},
            }
        ],
    )

    assert response.status_code == 422
    body = response.body.decode("utf-8")
    assert "MODEL_SCHEMA_VALIDATION_FAILED" in body
    assert "boom" in body
