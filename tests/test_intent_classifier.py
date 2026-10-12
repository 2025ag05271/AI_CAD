import asyncio
import numpy as np
import pytest

from catia_agent import intent_classifier


# ---------------------------------------------------------
# Fixtures
# ---------------------------------------------------------

@pytest.fixture(autouse=True)
def _reset_intent_matrices_cache(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # The exemplar matrix cache is a module-level singleton; clear it
    # and shrink the exemplar set so tests are fast and deterministic
    # instead of depending on the full production exemplar lists.
    monkeypatch.setattr(
        intent_classifier,
        "_intent_matrices",
        None,
    )
    monkeypatch.setattr(
        intent_classifier,
        "_classifier_unavailable_until",
        0.0,
    )
    monkeypatch.setattr(
        intent_classifier,
        "EXEMPLAR_INTENTS",
        {
            intent_classifier.AGENTIC_INTENT: [
                "open the document"
            ],
            intent_classifier.NON_AGENTIC_INTENT: [
                "how do i create a pad"
            ],
        },
    )


def _stub_embeddings(
    monkeypatch: pytest.MonkeyPatch,
    *,
    agentic_vector: list[float],
    non_agentic_vector: list[float],
    query_vector: list[float],
) -> None:
    async def fake_embed_texts(
        texts: list[str],
    ) -> np.ndarray:
        if texts == ["open the document"]:
            return np.asarray([agentic_vector], dtype=np.float32)
        if texts == ["how do i create a pad"]:
            return np.asarray([non_agentic_vector], dtype=np.float32)
        return np.asarray(
            [query_vector for _ in texts],
            dtype=np.float32,
        )

    monkeypatch.setattr(
        intent_classifier,
        "_embed_texts",
        fake_embed_texts,
    )


async def _raise_embedding_error(
    texts: list[str],
) -> np.ndarray:
    raise ConnectionError("The local embedding model failed.")


# ---------------------------------------------------------
# Pure helper functions
# ---------------------------------------------------------

def test_normalize_produces_unit_vectors() -> None:
    normalized = intent_classifier._normalize(
        np.array([[3.0, 4.0], [0.0, 0.0]])
    )

    assert normalized[0] == pytest.approx(
        [0.6, 0.8]
    )
    assert normalized[1] == pytest.approx(
        [0.0, 0.0]
    )


def test_strip_file_paths_replaces_quoted_path_with_spaces() -> None:
    message = (
        "can you please open a document for me? it lives in this "
        'path "C:\\Users\\me\\OneDrive - Autoliv\\Part.CATPart"'
    )

    stripped = intent_classifier._strip_file_paths(message)

    assert stripped == (
        "can you please open a document for me? it lives in this "
        "path a file path"
    )


def test_strip_file_paths_replaces_unquoted_path() -> None:
    stripped = intent_classifier._strip_file_paths(
        "Open C:\\TestData\\Gearbox.CATProduct"
    )

    assert stripped == "Open a file path"


def test_strip_file_paths_replaces_unc_path() -> None:
    stripped = intent_classifier._strip_file_paths(
        "Download the CATProduct from "
        "\\\\server\\share\\folder\\file.CATProduct"
    )

    assert stripped == "Download the CATProduct from a file path"


def test_strip_file_paths_leaves_path_free_text_unchanged() -> None:
    message = "How is the weather looking like in India?"

    assert (
        intent_classifier._strip_file_paths(message) == message
    )


def test_top_k_mean_averages_strongest_scores() -> None:
    scores = np.array([0.1, 0.9, 0.5, 0.2])

    assert intent_classifier._top_k_mean(
        scores, 2
    ) == pytest.approx((0.9 + 0.5) / 2)


# ---------------------------------------------------------
# classify_request_intent
# ---------------------------------------------------------

@pytest.mark.asyncio
async def test_classify_request_intent_selects_agentic(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _stub_embeddings(
        monkeypatch,
        agentic_vector=[1.0, 0.0],
        non_agentic_vector=[0.0, 1.0],
        query_vector=[1.0, 0.0],
    )

    result = await intent_classifier.classify_request_intent(
        "Open the gearbox."
    )

    assert result is not None
    intent, scores = result
    assert intent == intent_classifier.AGENTIC_INTENT
    assert (
        scores[intent_classifier.AGENTIC_INTENT]
        > scores[intent_classifier.NON_AGENTIC_INTENT]
    )


@pytest.mark.asyncio
async def test_classify_request_intent_strips_file_path_before_embedding(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured_queries: list[str] = []

    async def fake_embed_texts(
        texts: list[str],
    ) -> np.ndarray:
        if texts == ["open the document"]:
            return np.asarray([[1.0, 0.0]])
        if texts == ["how do i create a pad"]:
            return np.asarray([[0.0, 1.0]])
        captured_queries.extend(texts)
        return np.asarray([[1.0, 0.0] for _ in texts])

    monkeypatch.setattr(
        intent_classifier,
        "_embed_texts",
        fake_embed_texts,
    )

    await intent_classifier.classify_request_intent(
        'Open "C:\\Users\\me\\Project\\Part.CATPart" please.'
    )

    assert captured_queries == [
        "Open a file path please."
    ]


@pytest.mark.asyncio
async def test_classify_request_intent_selects_non_agentic(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _stub_embeddings(
        monkeypatch,
        agentic_vector=[1.0, 0.0],
        non_agentic_vector=[0.0, 1.0],
        query_vector=[0.0, 1.0],
    )

    result = await intent_classifier.classify_request_intent(
        "How do I create a pad?"
    )

    assert result is not None
    intent, _ = result
    assert intent == intent_classifier.NON_AGENTIC_INTENT


@pytest.mark.asyncio
async def test_classify_request_intent_prefers_safer_route_below_margin(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Query sits almost equidistant between both exemplars: above the
    # agentic threshold but not separated from the general-chat score
    # by the required margin.
    monkeypatch.setattr(
        intent_classifier.settings,
        "intent_classifier_agentic_threshold",
        0.4,
    )
    monkeypatch.setattr(
        intent_classifier.settings,
        "intent_classifier_agentic_margin",
        0.2,
    )

    _stub_embeddings(
        monkeypatch,
        agentic_vector=[1.0, 0.0],
        non_agentic_vector=[0.9, 0.1],
        query_vector=[1.0, 0.0],
    )

    result = await intent_classifier.classify_request_intent(
        "Ambiguous message."
    )

    assert result is not None
    intent, _ = result
    assert intent == intent_classifier.NON_AGENTIC_INTENT


@pytest.mark.asyncio
async def test_classify_request_intent_returns_none_on_embedding_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        intent_classifier,
        "_embed_texts",
        _raise_embedding_error,
    )

    result = await intent_classifier.classify_request_intent(
        "Open the gearbox."
    )

    assert result is None


@pytest.mark.asyncio
async def test_classify_request_intent_skips_embeddings_during_cooldown(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = {"count": 0}

    async def failing_embed_texts(
        texts: list[str],
    ) -> np.ndarray:
        calls["count"] += 1
        raise ConnectionError("The local embedding model failed.")

    monkeypatch.setattr(
        intent_classifier,
        "_embed_texts",
        failing_embed_texts,
    )

    assert await intent_classifier.classify_request_intent("a") is None
    assert await intent_classifier.classify_request_intent("b") is None
    assert calls["count"] == 1


@pytest.mark.asyncio
async def test_classify_request_intent_returns_none_on_timeout(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def hanging_embed_texts(
        texts: list[str],
    ) -> np.ndarray:
        await asyncio.sleep(10)
        return np.empty((0, 0))

    monkeypatch.setattr(
        intent_classifier,
        "_embed_texts",
        hanging_embed_texts,
    )
    monkeypatch.setattr(
        intent_classifier.settings,
        "intent_classifier_timeout_seconds",
        0.01,
    )

    assert await intent_classifier.classify_request_intent(
        "Open the gearbox."
    ) is None


# ---------------------------------------------------------
# is_agentic_request
# ---------------------------------------------------------

@pytest.mark.asyncio
async def test_is_agentic_request_true_for_agentic_intent(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _stub_embeddings(
        monkeypatch,
        agentic_vector=[1.0, 0.0],
        non_agentic_vector=[0.0, 1.0],
        query_vector=[1.0, 0.0],
    )

    assert (
        await intent_classifier.is_agentic_request(
            "Open the gearbox."
        )
        is True
    )


@pytest.mark.asyncio
async def test_is_agentic_request_false_for_non_agentic_intent(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _stub_embeddings(
        monkeypatch,
        agentic_vector=[1.0, 0.0],
        non_agentic_vector=[0.0, 1.0],
        query_vector=[0.0, 1.0],
    )

    assert (
        await intent_classifier.is_agentic_request(
            "How do I create a pad?"
        )
        is False
    )


@pytest.mark.asyncio
async def test_is_agentic_request_none_when_disabled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        intent_classifier.settings,
        "enable_embedding_intent_classifier",
        False,
    )

    async def fail_if_called(
        texts: list[str],
    ) -> np.ndarray:
        raise AssertionError(
            "Embeddings must not be called when disabled."
        )

    monkeypatch.setattr(
        intent_classifier,
        "_embed_texts",
        fail_if_called,
    )

    assert (
        await intent_classifier.is_agentic_request(
            "Open the gearbox."
        )
        is None
    )


@pytest.mark.asyncio
async def test_is_agentic_request_none_on_embedding_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        intent_classifier,
        "_embed_texts",
        _raise_embedding_error,
    )

    assert (
        await intent_classifier.is_agentic_request(
            "Open the gearbox."
        )
        is None
    )
