from __future__ import annotations

import hashlib
import json
import os
import shutil
from pathlib import Path


MODEL_NAME = "qllama/bge-small-en-v1.5"
MODEL_TAG = "latest"
MODEL_FILENAME = "qllama-bge-small-en-v1.5.gguf"
EXPECTED_SHA256 = "9f4a71e833a2181c810ad36dcd94bee035715643c75a6eb5cbe7620e958a5a21"
ROOT = Path(__file__).resolve().parents[1]
DESTINATION = ROOT / "build_assets" / MODEL_FILENAME


def _find_source_model() -> Path:
    configured_path = os.environ.get("CATIA_AGENT_INTENT_CLASSIFIER_GGUF_PATH")
    if configured_path:
        return Path(configured_path)

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
        / Path(*MODEL_NAME.split("/"))
        / MODEL_TAG
    )
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))

    for layer in manifest.get("layers", []):
        if (
            layer.get("mediaType") == "application/vnd.ollama.image.model"
            and layer.get("digest") == f"sha256:{EXPECTED_SHA256}"
        ):
            return models_root / "blobs" / f"sha256-{EXPECTED_SHA256}"

    raise FileNotFoundError(
        f"The expected {MODEL_NAME}:{MODEL_TAG} GGUF layer was not found."
    )


def main() -> None:
    source = _find_source_model()
    if not source.is_file():
        raise FileNotFoundError(f"BGE model file not found: {source}")

    digest = hashlib.sha256(source.read_bytes()).hexdigest()
    if digest != EXPECTED_SHA256:
        raise ValueError("The BGE GGUF checksum did not match the pinned model.")

    DESTINATION.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(source, DESTINATION)
    print(f"Prepared verified BGE model asset: {DESTINATION}")


if __name__ == "__main__":
    main()