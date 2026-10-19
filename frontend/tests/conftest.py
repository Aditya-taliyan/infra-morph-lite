import sys
from pathlib import Path

# Make frontend/api_client.py importable as "api_client", the way
# `streamlit run frontend/app.py` sees it. Appended, not prepended, so
# frontend/app.py never shadows the backend's "app" package.
FRONTEND_DIR = Path(__file__).resolve().parents[1]
if str(FRONTEND_DIR) not in sys.path:
    sys.path.append(str(FRONTEND_DIR))
