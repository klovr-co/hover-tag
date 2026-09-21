"""Exercise an installed release from outside the development checkout."""
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

with tempfile.TemporaryDirectory(prefix="Tag smoke ") as temporary:
    directory = Path(temporary)
    env = dict(os.environ, TAG_HOME=str(directory / "home"))
    subprocess.run([sys.executable, str(ROOT / "scripts/tag_install.py"), "--source", str(ROOT),
                    "--bin-dir", str(directory / "bin")], env=env, check=True)
    command = directory / "bin" / ("tag.cmd" if os.name == "nt" else "tag")
    subprocess.run([str(command), "version"], cwd=directory, env=env, check=True)
    env.update(OPENTAG_BACKEND="codex", MFS_URL="http://127.0.0.1:13619",
               MFS_ALLOWED_SCOPES="file://local/test", SLACK_ALLOWED_USER_IDS="UOWNER")
    subprocess.run([str(command), "doctor", "--offline"], cwd=directory, env=env, check=True)
    current = json.loads((directory / "home/current.json").read_text())
    subprocess.run([current["python"], "-c", "import slack_bolt, psutil, mfs_server"], check=True)
