$ErrorActionPreference = "Stop"

if ($env:CATIA_AGENT_GPM_SOURCE) {
	python scripts/convert_gpm_catalog.py --source $env:CATIA_AGENT_GPM_SOURCE
}

python scripts/prepare_worker_model.py
python -m PyInstaller --noconfirm --clean catia_agent_worker.spec