import os
import tempfile
from pathlib import Path

TMP_DIR = Path(__file__).resolve().parents[1] / ".pytest-tmp"
TMP_DIR.mkdir(exist_ok=True)
os.environ.setdefault("TMPDIR", str(TMP_DIR))
tempfile.tempdir = str(TMP_DIR)
