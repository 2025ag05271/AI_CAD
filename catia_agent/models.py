from typing import Any, Literal
from pydantic import BaseModel, Field


# ---------------------------------------------------------
# Tool arguments
# ---------------------------------------------------------

class OpenCadDocumentArguments(BaseModel):
    filePath: str


class GetActiveDocumentArguments(BaseModel):
    pass


# ---------------------------------------------------------
# CATIA actions
# ---------------------------------------------------------

class OpenCadDocumentAction(BaseModel):
    action: Literal["open_cad_document"]
    arguments: OpenCadDocumentArguments


class GetActiveDocumentAction(BaseModel):
    action: Literal["get_active_document"]
    arguments: GetActiveDocumentArguments

class CloseCadDocumentArguments(BaseModel):
    targetMode: Literal[
        "active",
        "named",
        "all",
    ]

    documentName: str | None = None

class CloseCadDocumentAction(BaseModel):
    action: Literal["close_cad_document"]
    arguments: CloseCadDocumentArguments

class CreateCadDocumentArguments(BaseModel):
    documentType: Literal[
        "CATProduct",
        "CATPart",
        "CATDrawing",
    ]

class CreateCadDocumentAction(BaseModel):
    action: Literal["create_cad_document"]
    arguments: CreateCadDocumentArguments

CatiaAction = (
    OpenCadDocumentAction
    | GetActiveDocumentAction
    | CloseCadDocumentAction
    | CreateCadDocumentAction
)

class RequestClarificationArguments(BaseModel):
    question: str
    missingField: str


class RequestClarificationAction(BaseModel):
    action: Literal["request_clarification"]
    arguments: RequestClarificationArguments


class RouteToGeneralChatArguments(BaseModel):
    pass


class RouteToGeneralChatAction(BaseModel):
    action: Literal["route_to_general_chat"]
    arguments: RouteToGeneralChatArguments


# ---------------------------------------------------------
# Planner results
# ---------------------------------------------------------

class ActionRequiredResult(BaseModel):
    status: Literal["action_required"]
    action: CatiaAction


class UnsupportedRequestResult(BaseModel):
    status: Literal["unsupported_request"]
    message: str

class InvalidRequestResult(BaseModel):
    status: Literal["invalid_request"]
    message: str
    
class ClarificationRequiredResult(BaseModel):
    status: Literal["clarification_required"]
    message: str
    missingField: str

class NonAgenticResult(BaseModel):
    status: Literal["non_agentic"]
    message: str

PlannerResult = (
    ActionRequiredResult
    | NonAgenticResult
    | ClarificationRequiredResult
    | UnsupportedRequestResult
    | InvalidRequestResult
)

# ---------------------------------------------------------
# CATIA runtime context
# ---------------------------------------------------------

class CatiaDocumentContext(BaseModel):
    name: str
    fullPath: str | None = None
    documentType: str | None = None


class CatiaSelectionContext(BaseModel):
    name: str
    objectType: str | None = None


class CatiaContext(BaseModel):
    isRunning: bool
    activeDocument: CatiaDocumentContext | None = None

    openDocuments: list[CatiaDocumentContext] = Field(
        default_factory=list
    )

    selectedObjects: list[CatiaSelectionContext] = Field(
        default_factory=list
    )

# ---------------------------------------------------------
# HTTP API models
# ---------------------------------------------------------

class AgentRunRequest(BaseModel):
    sessionId: str
    message: str
    catiaContext: CatiaContext | None = None
    

# ---------------------------------------------------------
# External action execution results
# ---------------------------------------------------------

class ActionExecutionResultRequest(BaseModel):
    actionId: str
    success: bool
    data: dict[str, Any] | None = None
    errorCode: str | None = None
    errorMessage: str | None = None


class ActionCompletedResponse(BaseModel):
    status: Literal["completed"]
    sessionId: str
    runId: str
    actionId: str
    message: str
    data: dict[str, Any] | None = None


class ActionFailedResponse(BaseModel):
    status: Literal["execution_failed"]
    sessionId: str
    runId: str
    actionId: str
    message: str
    errorCode: str | None = None