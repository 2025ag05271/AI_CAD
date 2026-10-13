from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import threading
import time
from pathlib import Path
from typing import Any

import numpy as np
from llama_cpp import Llama

from catia_agent.config import settings
from catia_agent.logging_config import Stopwatch, get_logger, log_event


logger = get_logger(__name__)


# -------------------------------------------------------------------------
# Ask-vs-agent intent classification
#
# Decides whether a user message is a concrete CATIA action request
# ("agentic") or a general/informational message, using the qllama
# BGE-small GGUF in-process instead of spending a model round trip on
# that decision. The GGUF is loaded directly;
# the tool-calling model is always Azure AI Foundry.
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
        "How do I open a master drawing in CATIA?",
        "What is a master drawing used for?",
        "How can I find the correct master drawing revision?",
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
        "Can you please open the master drawing of part 674898900A?",
        "Open the master drawing for part number 674089800A revision 01.",
        "Please show me the master drawing for 674089800A.",
        "Bring up the master drawing for part 674898900A.",
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


_MODEL_FILENAME = "qllama-bge-small-en-v1.5.gguf"
_MODEL_LOCK = threading.Lock()
_embedding_model: Llama | None = None


def _find_ollama_model_file() -> Path | None:
    model_name, _, model_tag = settings.intent_classifier_model.partition(":")
    model_tag = model_tag or "latest"
    models_root = Path(
        os.environ.get(
            "OLLAMA_MODELS",
            str(Path.home() / ".ollama" / "models"),
        )
    )
    manifest_path = (
        models_root
        / "manifests"
        / "registry.ollama.ai"
        / Path(*model_name.split("/"))
        / model_tag
    )

    try:
        manifest = json.loads(
            manifest_path.read_text(encoding="utf-8")
        )
    except (OSError, json.JSONDecodeError):
        return None

    for layer in manifest.get("layers", []):
        if layer.get("mediaType") != "application/vnd.ollama.image.model":
            continue

        digest = str(layer.get("digest", ""))
        if not digest.startswith("sha256:"):
            continue

        model_path = models_root / "blobs" / digest.replace(":", "-")
        if model_path.is_file():
            return model_path

    return None


def _resolve_model_path() -> Path:
    if settings.intent_classifier_gguf_path:
        configured_path = Path(settings.intent_classifier_gguf_path)
        if configured_path.is_file():
            return configured_path
        raise FileNotFoundError(
            "The configured intent-classifier GGUF model file was not found."
        )

    packaged_path = Path(__file__).parent / "assets" / _MODEL_FILENAME
    if packaged_path.is_file():
        return packaged_path

    local_ollama_path = _find_ollama_model_file()
    if local_ollama_path is not None:
        return local_ollama_path

    raise FileNotFoundError(
        "The bundled BGE GGUF model is missing. Rebuild the executable "
        "with the model asset or configure CATIA_AGENT_INTENT_CLASSIFIER_GGUF_PATH."
    )


def _embed_texts_sync(texts: list[str]) -> list[list[float]]:
    global _embedding_model

    with _MODEL_LOCK:
        if _embedding_model is None:
            _embedding_model = Llama(
                model_path=str(_resolve_model_path()),
                embedding=True,
                n_ctx=512,
                verbose=False,
            )

        return _embedding_model.embed(
            texts,
            normalize=False,
            truncate=True,
        )


async def _embed_texts(texts: list[str]) -> np.ndarray:
    vectors = await asyncio.to_thread(
        _embed_texts_sync,
        texts,
    )
    return np.asarray(vectors, dtype=np.float32)

# Built lazily (not at import time); the first classification call pays
# the one-time cost of loading the local GGUF and embedding exemplars.
_intent_matrices: dict[str, np.ndarray] | None = None
_intent_matrices_lock = asyncio.Lock()

# Monotonic deadline before which the classifier is skipped after a
# failure, so a broken local model doesn't cost every request a timeout.
_classifier_unavailable_until: float = 0.0


async def _get_intent_matrices() -> dict[str, np.ndarray]:
    global _intent_matrices

    if _intent_matrices is not None:
        return _intent_matrices

    async with _intent_matrices_lock:
        if _intent_matrices is not None:
            return _intent_matrices

        matrices: dict[str, np.ndarray] = {}
        stopwatch = Stopwatch()

        for intent, phrases in EXEMPLAR_INTENTS.items():
            normalized_phrases = [
                _strip_file_paths(phrase) for phrase in phrases
            ]
            vectors = await _embed_texts(normalized_phrases)
            matrices[intent] = _normalize(vectors)

        log_event(
            logger,
            "intent_exemplars_embedded",
            durationMs=stopwatch.elapsed_ms,
            exemplarCount=sum(
                len(phrases)
                for phrases in EXEMPLAR_INTENTS.values()
            ),
        )

        _intent_matrices = matrices
        return matrices


async def warmup_intent_classifier() -> None:
    """Load the bundled model and exemplar vectors before worker readiness."""

    if settings.enable_embedding_intent_classifier:
        await _get_intent_matrices()


async def classify_request_intent(
    user_message: str,
) -> tuple[str, dict[str, float]] | None:
    """
    Classify a message as AGENTIC_INTENT or NON_AGENTIC_INTENT.

    Returns None if the local embedding call failed, timed out, or is
    in its post-failure cooldown, so callers can fall back to
    letting the model decide instead of failing the request.
    """

    global _classifier_unavailable_until

    if time.monotonic() < _classifier_unavailable_until:
        log_event(
            logger,
            "intent_classifier_skipped_cooldown",
            level=logging.DEBUG,
        )
        return None

    stopwatch = Stopwatch()

    try:
        async with asyncio.timeout(
            settings.intent_classifier_timeout_seconds
        ):
            matrices = await _get_intent_matrices()
            query_vector = _normalize(
                await _embed_texts(
                    [_strip_file_paths(user_message)]
                )
            )[0]

    except Exception as exc:
        _classifier_unavailable_until = (
            time.monotonic()
            + settings.intent_classifier_failure_cooldown_seconds
        )

        logger.warning(
            "intent_classifier_embedding_failed",
            extra={
                "exceptionType": type(exc).__name__,
                "durationMs": stopwatch.elapsed_ms,
                "cooldownSeconds": (
                    settings
                    .intent_classifier_failure_cooldown_seconds
                ),
            },
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

    stopwatch = Stopwatch()

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
            "durationMs": stopwatch.elapsed_ms,
            "agenticScore": round(
                scores[AGENTIC_INTENT], 4
            ),
            "generalChatScore": round(
                scores[NON_AGENTIC_INTENT], 4
            ),
        },
    )

    return intent == AGENTIC_INTENT
