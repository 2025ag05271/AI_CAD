from __future__ import annotations

import asyncio
import re
from typing import Any

import numpy as np
from langchain_ollama import OllamaEmbeddings

from catia_agent.config import settings
from catia_agent.logging_config import get_logger


logger = get_logger(__name__)


# -------------------------------------------------------------------------
# Ask-vs-agent intent classification
#
# Decides whether a user message is a concrete CATIA action request
# ("agentic") or a general/informational message, using a local Ollama
# embedding model (qllama/bge-small-en-v1.5 by default) instead of
# spending a model round trip on that decision. Adapted from
# tests/bge_embedding_test.py. This is independent of model_provider:
# it always talks to the local Ollama install for embeddings, even
# when the chat model_provider is azure_foundry.
# -------------------------------------------------------------------------

AGENTIC_INTENT = "catia_agent_command"
NON_AGENTIC_INTENT = "catia_knowledge_rag"

# Long, unusual absolute file paths (real CATIA files often live many
# folders deep, e.g. under a synced OneDrive tree) dilute the embedding
# signal that otherwise clearly separates "open/download/create ..."
# commands from knowledge questions, pulling both intent scores toward
# a compressed, nearly-tied high band. Replacing any Windows/UNC path
# with a flat placeholder before embedding keeps the classification
# focused on the surrounding natural-language wording instead of on an
# arbitrarily long, path-specific token sequence. Applied identically
# to exemplars and to the incoming message so both sides are embedded
# on the same normalized footing.
_FILE_PATH_PLACEHOLDER = "a file path"

_QUOTED_FILE_PATH_PATTERN = re.compile(
    r"""(["'])(?:[A-Za-z]:[\\/]|\\\\)[^"']*\1"""
)

_UNQUOTED_FILE_PATH_PATTERN = re.compile(
    r"""(?:[A-Za-z]:[\\/]|\\\\)[^\s"'<>|]+"""
)


def _strip_file_paths(text: str) -> str:
    text = _QUOTED_FILE_PATH_PATTERN.sub(
        _FILE_PATH_PLACEHOLDER, text
    )
    text = _UNQUOTED_FILE_PATH_PATTERN.sub(
        _FILE_PATH_PLACEHOLDER, text
    )
    return text


# Keep examples diverse, realistic, and close to production wording.
EXEMPLAR_INTENTS: dict[str, list[str]] = {
    NON_AGENTIC_INTENT: [
        # Part Design and Sketcher
        "How do I create a pad from a sketch in CATIA?",
        "How can I create a pocket in Part Design?",
        "What is the difference between a pad and a shaft feature?",
        "How do I fully constrain a sketch?",
        "Why is my CATIA sketch under-constrained?",
        "How can I create a circular pattern of holes?",
        "How do I create a rib or slot from a profile?",
        "How can I edit the thickness of a shell feature?",
        "Why does CATIA report that the sketch is not closed?",
        "How do I create a datum plane at an offset?",

        # Assembly and product structure
        "How do I create an assembly in CATIA?",
        "What is the difference between Fix, Coincidence, and Contact constraints?",
        "Why is my CATProduct not updating correctly?",
        "How do I replace a component in an assembly?",
        "How can I analyze clashes between assembly components?",
        "How do I move a component manually in Assembly Design?",
        "How can I find broken links in a CATProduct?",
        "What is the difference between a CATPart and a CATProduct?",
        "How do I use contextual design in a product assembly?",
        "Why is an assembly constraint inconsistent?",

        # Drafting, tolerancing, and standards
        "How do I generate a drawing from a 3D model?",
        "How can I create section and detail views in Drafting?",
        "How do I add dimensions and tolerances to a CATDrawing?",
        "What are the criteria for applying geometric tolerances in a drawing?",
        "Please remind me of the rules for tolerancing a technical drawing.",
        "How should datum references be selected for GD&T?",
        "What is the difference between flatness, parallelism, and perpendicularity?",
        "How do I create a title block in CATIA Drafting?",
        "Why are drawing views not updating from the 3D model?",
        "How can I change a drawing standard from ISO to ASME?",
        "What are CATIA drafting standards and best practices?",

        # Surface and shape design
        "How do I create a surface in Generative Shape Design?",
        "What is the difference between join, trim, and split operations?",
        "How can I repair gaps between surfaces?",
        "Why does a surface join fail in CATIA?",
        "How do I create a sweep along a guide curve?",
        "How can I check surface continuity in GSD?",
        "What is the difference between G0, G1, and G2 continuity?",
        "How do I extract a boundary from a surface?",

        # Measurement, properties, and DMU
        "How can I calculate center of gravity using VBA?",
        "How do I measure surface area and volume?",
        "How can I calculate mass and inertia properties of a product?",
        "Why is the center of gravity result incorrect?",
        "How does DMU Kinematics work in CATIA?",
        "How do I define joints in a kinematic mechanism?",
        "How can I run a clearance analysis in DMU Space Analysis?",
        "What is the Measure Inertia command used for?",

        # Import, export, files, and interoperability
        "How do I import a STEP file in CATIA?",
        "How can I export a CATPart to STEP format?",
        "What file formats can CATIA open?",
        "Why does an imported IGES file contain broken surfaces?",
        "How do I save a CATDrawing as PDF?",
        "What settings should I use when exporting a STEP file?",
        "How can I convert a CATProduct to a 3DXML file?",
        "Why are product links missing after moving CATIA files?",

        # Automation, VBA, APIs, and troubleshooting
        "How can I automate CATIA using VBA macros?",
        "How do I get the center of gravity of a CATProduct using VBA?",
        "How can I access CATIA parameters programmatically?",
        "How do I use Product Structure APIs?",
        "How can I iterate through all products in an assembly using VBA?",
        "What does GetTechnologicalObject return in CATIA automation?",
        "How do I obtain a Measurable object in context?",
        "Why does my CATIA macro raise an object required error?",
        "How can I update a part through the CATIA API?",
        "How do I read custom properties from a CATProduct?",
        "What causes CATIA update errors and how can I troubleshoot them?",
        "Why does CATIA crash when opening a large assembly?",
        "How can I improve CATIA performance for large products?",
        "What should I check when CATIA cannot acquire a license?",
    ],
    AGENTIC_INTENT: [
        # Open commands
        "Can you please open a CAD document for me?",
        "Open C:\\TestData\\Gearbox.CATProduct",
        "Open the bearing housing CATPart.",
        "Load the assembly from my local folder.",
        "Please open the latest downloaded CATDrawing.",
        "Open product 6328629 revision CAD000.",
        "Launch CATIA and open the active project assembly.",

        # Create commands
        "Create a new part document.",
        "Create an empty CATProduct.",
        "Make a new drawing document using the ISO template.",
        "Start a new CATIA assembly for me.",
        "Create a CATPart named Bracket.",
        "Generate a new drawing from the active part.",

        # Close, save, and lifecycle commands
        "Close the active document.",
        "Close all open documents.",
        "Save the current CATPart.",
        "Save all modified CATIA documents.",
        "Save the active document as Bracket_v2.CATPart.",
        "Close CATIA without saving the current file.",
        "Export the active drawing to PDF now.",

        # ENOVIA and retrieval commands
        "Download CAD file 6328629 revision CAD000 from ENOVIA.",
        "Retrieve part number 65478 revision CAD002 from ENOVIA.",
        "Fetch the latest released revision of this CAD model.",
        "Download the CATProduct associated with AO ID 12345.",
        "Open the latest revision of part 778899 from ENOVIA.",
        "Check out the selected CAD document from ENOVIA.",

        # Execute actions and automation
        "Start the SAB model for AO ID 12345.",
        "Run the center of gravity macro on the active product.",
        "Execute the update macro for all open documents.",
        "Measure the active product and return its center of gravity.",
        "Update the active CATProduct now.",
        "Run clash analysis on the currently open assembly.",
        "Apply the company drafting standard to this drawing.",
        "Insert the downloaded component into the active assembly.",
        "Replace the selected component with the latest revision.",
        "Move the selected product instance to coordinates 0, 0, 0.",
        "Create a support package from the active CATIA session.",

        # Conversational/indirect phrasing that explains where a file
        # lives, rather than a short imperative command. Long file
        # paths are stripped before embedding (see
        # _strip_file_paths), but the surrounding wording still needs
        # its own agentic-intent coverage among the exemplars.
        "Can you please open a document for me? It lives in this "
        "path C:\\Users\\me\\Documents\\Project\\Part.CATPart",
        "I have a CATPart I need opened, it's located at "
        "D:\\Data\\Bracket.CATPart",
        "Please open the file located at "
        "C:\\Shared\\Models\\Assembly.CATProduct for me",
        "There's a product I'd like opened, you can find it at "
        "C:\\Projects\\Gearbox\\Gearbox.CATProduct",
        "Could you download this for me? It's part number 65478 "
        "revision CAD002 from ENOVIA.",
    ],
}


def _normalize(vectors: Any) -> np.ndarray:
    """L2-normalize a vector or matrix."""
    array = np.asarray(vectors, dtype=np.float32)
    norm = np.linalg.norm(array, axis=-1, keepdims=True)
    return array / np.maximum(norm, 1e-12)


def _top_k_mean(scores: np.ndarray, k: int) -> float:
    """Average the strongest matches to reduce dependence on one exemplar."""
    bounded_k = min(k, scores.size)
    return float(
        np.mean(np.partition(scores, -bounded_k)[-bounded_k:])
    )


embeddings = OllamaEmbeddings(
    model=settings.intent_classifier_model,
    base_url=settings.ollama_base_url,
)

# Built lazily (not at import time) so importing this module never makes
# a network call; the first classification call pays the one-time cost
# of embedding every exemplar phrase.
_intent_matrices: dict[str, np.ndarray] | None = None
_intent_matrices_lock = asyncio.Lock()


async def _get_intent_matrices() -> dict[str, np.ndarray]:
    global _intent_matrices

    if _intent_matrices is not None:
        return _intent_matrices

    async with _intent_matrices_lock:
        if _intent_matrices is not None:
            return _intent_matrices

        matrices: dict[str, np.ndarray] = {}

        for intent, phrases in EXEMPLAR_INTENTS.items():
            normalized_phrases = [
                _strip_file_paths(phrase) for phrase in phrases
            ]
            vectors = await embeddings.aembed_documents(
                normalized_phrases
            )
            matrices[intent] = _normalize(np.asarray(vectors))

        _intent_matrices = matrices
        return matrices


async def classify_request_intent(
    user_message: str,
) -> tuple[str, dict[str, float]] | None:
    """
    Classify a message as AGENTIC_INTENT or NON_AGENTIC_INTENT.

    Returns None if the local Ollama embedding call failed (for
    example, Ollama is not running), so callers can fall back to
    letting the model decide instead of failing the request.
    """

    try:
        matrices = await _get_intent_matrices()
        query_vector = _normalize(
            np.asarray(
                await embeddings.aembed_query(
                    _strip_file_paths(user_message)
                )
            )
        )

    except Exception:
        logger.exception(
            "intent_classifier_embedding_failed"
        )
        return None

    scores = {
        intent: _top_k_mean(
            matrix @ query_vector,
            settings.intent_classifier_top_k,
        )
        for intent, matrix in matrices.items()
    }

    agentic_score = scores[AGENTIC_INTENT]
    non_agentic_score = scores[NON_AGENTIC_INTENT]

    # Prefer the safer general-chat route when confidence or
    # separation from the general-chat score is insufficient.
    is_agentic = (
        agentic_score
        >= settings.intent_classifier_agentic_threshold
        and agentic_score - non_agentic_score
        >= settings.intent_classifier_agentic_margin
    )

    return (
        AGENTIC_INTENT if is_agentic else NON_AGENTIC_INTENT
    ), scores


async def is_agentic_request(
    user_message: str,
) -> bool | None:
    """
    Return True/False for a confident classification, or None if the
    classifier could not run (caller should fall back to the model).
    """

    if not settings.enable_embedding_intent_classifier:
        return None

    classification = await classify_request_intent(
        user_message
    )

    if classification is None:
        return None

    intent, scores = classification

    logger.info(
        "intent_classified",
        extra={
            "intent": intent,
            "agenticScore": round(
                scores[AGENTIC_INTENT], 4
            ),
            "generalChatScore": round(
                scores[NON_AGENTIC_INTENT], 4
            ),
        },
    )

    return intent == AGENTIC_INTENT
