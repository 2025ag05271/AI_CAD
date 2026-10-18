from __future__ import annotations

import json
from typing import Annotated, Any, Literal, TypeAlias

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    model_validator,
)

from catia_agent.config import settings
from catia_agent.gpm_catalog import is_approved_drawing_url
from catia_agent.utils import (
    validate_download_cad_inputs,
    validate_open_master_drawing_inputs,
    validate_start_model_inputs,
)


# -------------------------------------------------------------------------
# Shared types
# -------------------------------------------------------------------------

NonEmptyString: TypeAlias = Annotated[
    str,
    StringConstraints(
        strip_whitespace=True,
        min_length=1,
    ),
]

Identifier: TypeAlias = Annotated[
    str,
    StringConstraints(
        strip_whitespace=True,
        min_length=1,
        max_length=settings.max_session_id_characters,
        pattern=r"^[A-Za-z0-9._:-]+$",
    ),
]

RequestIdentifier: TypeAlias = Annotated[
    str,
    StringConstraints(
        strip_whitespace=True,
        min_length=1,
        max_length=settings.max_request_id_characters,
        pattern=r"^[A-Za-z0-9._:-]+$",
    ),
]

UserMessage: TypeAlias = Annotated[
    str,
    StringConstraints(
        strip_whitespace=True,
        min_length=1,
        max_length=settings.max_user_message_characters,
    ),
]


# -------------------------------------------------------------------------
# Base models
# -------------------------------------------------------------------------

class ApiModel(BaseModel):
    """
    Base model for API contracts.

    Unknown fields are rejected so contract drift between Python,
    MCP, and .NET is detected immediately.
    """

    model_config = ConfigDict(
        extra="forbid",
        populate_by_name=True,
        validate_assignment=True,
        str_strip_whitespace=True,
    )


class InternalModel(BaseModel):
    """
    Base model for internal workflow data.
    """

    model_config = ConfigDict(
        extra="forbid",
        validate_assignment=True,
        str_strip_whitespace=True,
    )


# -------------------------------------------------------------------------
# Tool arguments
# -------------------------------------------------------------------------

class OpenCadDocumentArguments(InternalModel):
    filePath: NonEmptyString


class GetActiveDocumentArguments(InternalModel):
    pass


class CloseCadDocumentArguments(InternalModel):
    targetMode: Literal[
        "active",
        "named",
        "all",
    ]

    documentName: NonEmptyString | None = None

    @model_validator(mode="after")
    def validate_target_mode(
        self,
    ) -> CloseCadDocumentArguments:
        if self.targetMode == "named":
            if self.documentName is None:
                raise ValueError(
                    "documentName is required when targetMode "
                    "is named."
                )
        elif self.documentName is not None:
            # Bypass validate_assignment to avoid re-entering this validator.
            object.__setattr__(self, "documentName", None)

        return self


class CreateCadDocumentArguments(InternalModel):
    documentType: Literal[
        "CATProduct",
        "CATPart",
        "CATDrawing",
    ]


class DownloadCadArguments(InternalModel):
    partNumber: NonEmptyString | None = None
    partRevision: NonEmptyString | None = None
    documentType: NonEmptyString | None = None
    partTitle: NonEmptyString | None = None
    savePath: NonEmptyString | None = None

    @model_validator(mode="after")
    def validate_input_shape(
        self,
    ) -> DownloadCadArguments:
        (
            normalized_document_type,
            normalized_save_path,
        ) = (
            validate_download_cad_inputs(
                part_number=self.partNumber,
                part_revision=self.partRevision,
                part_title=self.partTitle,
                document_type=self.documentType,
                save_path=self.savePath,
            )
        )

        object.__setattr__(
            self,
            "documentType",
            normalized_document_type,
        )

        object.__setattr__(
            self,
            "savePath",
            normalized_save_path,
        )

        return self


class StartModelArguments(InternalModel):
    outsystemsId: NonEmptyString | None = None
    afisNumber: NonEmptyString | None = None
    customName: NonEmptyString | None = None
    partNumber: NonEmptyString | None = None
    partRevision: NonEmptyString | None = None
    savePath: NonEmptyString | None = None
    modelType: NonEmptyString

    @model_validator(mode="after")
    def validate_input_shape(
        self,
    ) -> StartModelArguments:
        (
            normalized_outsystems_id,
            normalized_part_number,
            normalized_part_revision,
            normalized_afis_number,
            normalized_custom_name,
            normalized_model_type,
            normalized_save_path,
        ) = validate_start_model_inputs(
            outsystems_id=self.outsystemsId,
            model_type=self.modelType,
            part_number=self.partNumber,
            afis_number=self.afisNumber,
            custom_name=self.customName,
            part_revision=self.partRevision,
            save_path=self.savePath,
        )

        object.__setattr__(
            self,
            "outsystemsId",
            normalized_outsystems_id,
        )

        object.__setattr__(
            self,
            "partNumber",
            normalized_part_number,
        )

        object.__setattr__(
            self,
            "partRevision",
            normalized_part_revision,
        )

        object.__setattr__(
            self,
            "afisNumber",
            normalized_afis_number,
        )

        object.__setattr__(
            self,
            "customName",
            normalized_custom_name,
        )

        object.__setattr__(
            self,
            "modelType",
            normalized_model_type,
        )
        
        object.__setattr__(
            self,
            "savePath",
            normalized_save_path,
        )

        return self


class OpenMasterDrawingArguments(InternalModel):
    partNumber: NonEmptyString
    partRevision: NonEmptyString | None = None
    partTitle: NonEmptyString | None = None
    plmStandardTitle: NonEmptyString | None = None
    masterDrawingOrOgdLabel: NonEmptyString | None = None
    masterDrawingOrOgdUrl: NonEmptyString | None = None

    @model_validator(mode="after")
    def validate_identifiers(
        self,
    ) -> OpenMasterDrawingArguments:
        part_number, part_revision = (
            validate_open_master_drawing_inputs(
                part_number=self.partNumber,
                part_revision=self.partRevision,
            )
        )

        object.__setattr__(self, "partNumber", part_number)
        object.__setattr__(self, "partRevision", part_revision)

        if (
            self.masterDrawingOrOgdUrl is not None
            and not is_approved_drawing_url(
                self.masterDrawingOrOgdUrl
            )
        ):
            raise ValueError(
                "masterDrawingOrOgdUrl must use an approved PLM drawing link."
            )

        return self


class RequestClarificationArguments(InternalModel):
    question: NonEmptyString

    missingField: Annotated[
        str,
        StringConstraints(
            strip_whitespace=True,
            min_length=1,
            max_length=100,
            pattern=r"^[A-Za-z][A-Za-z0-9_.-]*$",
        ),
    ]


class RouteToGeneralChatArguments(InternalModel):
    pass

class CompleteRequestArguments(InternalModel):
    pass

class ReportUnsupportedCatiaActionArguments(InternalModel):
    requestedCapability: NonEmptyString

    reason: Literal[
        "tool_not_available",
        "operation_not_allowed",
        "executor_not_available",
    ] = "tool_not_available"


# -------------------------------------------------------------------------
# CATIA actions
# -------------------------------------------------------------------------

class OpenCadDocumentAction(InternalModel):
    action: Literal["open_cad_document"]
    arguments: OpenCadDocumentArguments


class GetActiveDocumentAction(InternalModel):
    action: Literal["get_active_document"]
    arguments: GetActiveDocumentArguments


class CloseCadDocumentAction(InternalModel):
    action: Literal["close_cad_document"]
    arguments: CloseCadDocumentArguments


class CreateCadDocumentAction(InternalModel):
    action: Literal["create_cad_document"]
    arguments: CreateCadDocumentArguments


class DownloadCadAction(InternalModel):
    action: Literal["download_cad"]
    arguments: DownloadCadArguments


class StartModelAction(InternalModel):
    action: Literal["start_model"]
    arguments: StartModelArguments


class OpenMasterDrawingAction(InternalModel):
    action: Literal["open_master_drawing"]
    arguments: OpenMasterDrawingArguments


class RequestClarificationAction(InternalModel):
    action: Literal["request_clarification"]
    arguments: RequestClarificationArguments


class RouteToGeneralChatAction(InternalModel):
    action: Literal["route_to_general_chat"]
    arguments: RouteToGeneralChatArguments

class CompleteRequestAction(InternalModel):
    action: Literal["complete_request"]
    arguments: CompleteRequestArguments

class ReportUnsupportedCatiaAction(InternalModel):
    action: Literal["report_unsupported_catia_action"]
    arguments: ReportUnsupportedCatiaActionArguments


CatiaAction: TypeAlias = Annotated[
    OpenCadDocumentAction
    | GetActiveDocumentAction
    | CloseCadDocumentAction
    | CreateCadDocumentAction
    | DownloadCadAction
    | StartModelAction
    | OpenMasterDrawingAction,
    Field(discriminator="action"),
]

OrchestratorAction: TypeAlias = Annotated[
    RequestClarificationAction
    | RouteToGeneralChatAction
    | ReportUnsupportedCatiaAction
    | CompleteRequestAction,
    Field(discriminator="action"),
]

PlannerAction: TypeAlias = CatiaAction | OrchestratorAction


# -------------------------------------------------------------------------
# Planner results
# -------------------------------------------------------------------------

class ActionRequiredResult(InternalModel):
    status: Literal["action_required"]
    action: CatiaAction


class NonAgenticResult(InternalModel):
    status: Literal["non_agentic"]
    message: NonEmptyString

    routeTarget: Literal["general_chat"] = "general_chat"

    routeReason: Literal[
        "general_conversation",
        "informational_query",
        "non_catia_request",
    ] = "general_conversation"


class ClarificationRequiredResult(InternalModel):
    status: Literal["clarification_required"]
    message: NonEmptyString
    missingField: NonEmptyString


class UnsupportedRequestResult(InternalModel):
    status: Literal["unsupported_request"]
    message: NonEmptyString
    requestedCapability: str | None = None

    reason: Literal[
        "tool_not_available",
        "operation_not_allowed",
        "executor_not_available",
        "action_limit_reached",
    ] = "tool_not_available"


class InvalidRequestResult(InternalModel):
    status: Literal["invalid_request"]
    message: NonEmptyString


class PlanningErrorResult(InternalModel):
    status: Literal["planning_error"]
    message: NonEmptyString

    errorCode: Literal[
        "MODEL_RETURNED_NO_DECISION",
        "MODEL_RETURNED_MULTIPLE_DECISIONS",
        "MODEL_RETURNED_INVALID_TOOL",
        "MODEL_RETURNED_INVALID_ARGUMENTS",
    ]

class RequestCompletedResult(InternalModel):
    status: Literal["request_completed"]

PlannerResult: TypeAlias = Annotated[
    ActionRequiredResult
    | NonAgenticResult
    | ClarificationRequiredResult
    | UnsupportedRequestResult
    | InvalidRequestResult
    | PlanningErrorResult
    | RequestCompletedResult,
    Field(discriminator="status"),
]


# -------------------------------------------------------------------------
# CATIA runtime context
# -------------------------------------------------------------------------

class CatiaDocumentContext(ApiModel):
    name: NonEmptyString

    fullPath: str | None = None

    documentType: Literal[
        "CATProduct",
        "CATPart",
        "CATDrawing",
    ] | None = None


class CatiaSelectionContext(ApiModel):
    name: NonEmptyString
    objectType: str | None = None


class CatiaContext(ApiModel):
    isRunning: bool

    activeDocument: CatiaDocumentContext | None = None

    openDocuments: list[CatiaDocumentContext] = Field(
        default_factory=list,
        max_length=500,
    )

    selectedObjects: list[CatiaSelectionContext] = Field(
        default_factory=list,
        max_length=1000,
    )

    capturedAt: str | None = None

    contextVersion: int | None = Field(
        default=None,
        ge=0,
    )

    @model_validator(mode="after")
    def validate_runtime_state(
        self,
    ) -> CatiaContext:
        if not self.isRunning:
            if self.activeDocument is not None:
                raise ValueError(
                    "activeDocument must be null when CATIA "
                    "is not running."
                )

            if self.openDocuments:
                raise ValueError(
                    "openDocuments must be empty when CATIA "
                    "is not running."
                )

            if self.selectedObjects:
                raise ValueError(
                    "selectedObjects must be empty when CATIA "
                    "is not running."
                )

        if (
            self.activeDocument is not None
            and self.openDocuments
        ):
            active_document_exists = any(
                document.name == self.activeDocument.name
                for document in self.openDocuments
            )

            if not active_document_exists:
                raise ValueError(
                    "activeDocument must also appear in "
                    "openDocuments when openDocuments is supplied."
                )

        return self


# -------------------------------------------------------------------------
# HTTP request models
# -------------------------------------------------------------------------

class AgentRunRequest(ApiModel):
    sessionId: Identifier
    message: UserMessage
    catiaContext: CatiaContext | None = None


class ActionExecutionResultRequest(ApiModel):
    actionId: RequestIdentifier
    success: bool

    data: dict[str, Any] | None = None

    errorCode: Annotated[
        str,
        StringConstraints(
            strip_whitespace=True,
            min_length=1,
            max_length=100,
            pattern=r"^[A-Z0-9_]+$",
        ),
    ] | None = None

    errorMessage: Annotated[
        str,
        StringConstraints(
            strip_whitespace=True,
            min_length=1,
            max_length=2000,
        ),
    ] | None = None

    @model_validator(mode="after")
    def validate_result_consistency(
        self,
    ) -> ActionExecutionResultRequest:
        if self.success:
            if self.errorCode is not None:
                raise ValueError(
                    "errorCode must be null when success is true."
                )

            if self.errorMessage is not None:
                raise ValueError(
                    "errorMessage must be null when success is true."
                )
        else:
            if self.errorCode is None:
                raise ValueError(
                    "errorCode is required when success is false."
                )

            if self.errorMessage is None:
                raise ValueError(
                    "errorMessage is required when success is false."
                )

        # data is stored in run state and echoed into planner prompts.
        if self.data is not None:
            data_size = len(
                json.dumps(self.data, default=str).encode("utf-8")
            )

            if data_size > settings.max_completed_action_result_bytes:
                raise ValueError(
                    f"data is {data_size} bytes, which exceeds the "
                    "limit of "
                    f"{settings.max_completed_action_result_bytes} "
                    "bytes."
                )

        return self


# -------------------------------------------------------------------------
# Completed-action records
# -------------------------------------------------------------------------

class CompletedActionRecord(ApiModel):
    action: NonEmptyString
    actionId: RequestIdentifier
    arguments: dict[str, Any] = Field(default_factory=dict)
    message: NonEmptyString
    data: dict[str, Any] | None = None


# -------------------------------------------------------------------------
# Successful API responses
# -------------------------------------------------------------------------

class ActionRequiredResponse(ApiModel):
    status: Literal["action_required"]
    sessionId: Identifier
    runId: RequestIdentifier
    actionId: RequestIdentifier
    action: CatiaAction


class NonAgenticResponse(ApiModel):
    status: Literal["non_agentic"]
    sessionId: Identifier
    runId: RequestIdentifier
    message: NonEmptyString

    routeTarget: Literal["general_chat"] = "general_chat"

    routeReason: Literal[
        "general_conversation",
        "informational_query",
        "non_catia_request",
    ] = "general_conversation"

    completedActions: list[CompletedActionRecord] | None = None


class ClarificationRequiredResponse(ApiModel):
    status: Literal["clarification_required"]
    sessionId: Identifier
    runId: RequestIdentifier
    message: NonEmptyString
    missingField: NonEmptyString
    completedActions: list[CompletedActionRecord] | None = None


class UnsupportedRequestResponse(ApiModel):
    status: Literal["unsupported_request"]
    sessionId: Identifier
    runId: RequestIdentifier
    message: NonEmptyString
    requestedCapability: str | None = None

    reason: Literal[
        "tool_not_available",
        "operation_not_allowed",
        "executor_not_available",
        "action_limit_reached",
    ] = "tool_not_available"

    completedActions: list[CompletedActionRecord] | None = None


class InvalidRequestResponse(ApiModel):
    status: Literal["invalid_request"]
    sessionId: Identifier
    runId: RequestIdentifier
    message: NonEmptyString


class PlanningErrorResponse(ApiModel):
    status: Literal["planning_error"]
    sessionId: Identifier
    runId: RequestIdentifier
    message: NonEmptyString
    errorCode: NonEmptyString
    completedActions: list[CompletedActionRecord] | None = None


class ActionCompletedResponse(ApiModel):
    status: Literal["completed"]
    sessionId: Identifier
    runId: RequestIdentifier
    actionId: RequestIdentifier | None = None
    message: NonEmptyString
    data: dict[str, Any] | None = None

    completedActions: list[CompletedActionRecord] = Field(
        default_factory=list,
    )


class ActionFailedResponse(ApiModel):
    status: Literal["execution_failed"]
    sessionId: Identifier
    runId: RequestIdentifier
    actionId: RequestIdentifier
    message: NonEmptyString
    errorCode: NonEmptyString
    errorMessage: NonEmptyString

    completedActions: list[CompletedActionRecord] = Field(
        default_factory=list,
    )


AgentRunResponse: TypeAlias = Annotated[
    ActionRequiredResponse
    | NonAgenticResponse
    | ClarificationRequiredResponse
    | UnsupportedRequestResponse
    | InvalidRequestResponse
    | PlanningErrorResponse
    | ActionCompletedResponse
    | ActionFailedResponse,
    Field(discriminator="status"),
]


# -------------------------------------------------------------------------
# Error response models
# -------------------------------------------------------------------------

class ErrorDetail(ApiModel):
    status: NonEmptyString
    errorCode: NonEmptyString
    message: NonEmptyString
    technicalDetail: str | None = None
    validationErrors: list[dict[str, Any]] | None = None


class ErrorResponse(ApiModel):
    detail: ErrorDetail