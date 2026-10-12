$ErrorActionPreference = "Stop"

python scripts/prepare_worker_model.py
python -m PyInstaller --noconfirm --clean catia_agent_worker.spec