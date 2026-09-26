"""Exercise an installed release from outside the development checkout."""
import json
import os
import subprocess
import sys
import tempfile
import zipfile

from package_release import build_archive
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

with tempfile.TemporaryDirectory(prefix="Tag smoke ") as temporary:
    directory = Path(temporary)
    env = dict(os.environ, TAG_HOME=str(directory / "home"))
    # Exercise the shipped payload, not a checkout that can mask omitted files.
    archive = directory / "release.zip"
    source = directory / "release"
    build_archive(ROOT, archive)
    with zipfile.ZipFile(archive) as bundle:
        bundle.extractall(source)
        for entry in bundle.infolist():
            if entry.external_attr >> 16 & 0o111:
                (source / entry.filename).chmod(0o755)
    installer = ([sys.executable, str(source / "scripts/tag_install.py"), "--source", str(source)]
                 if os.name == "nt" else ["sh", str(source / "install.sh")])
    subprocess.run([*installer, "--bin-dir", str(directory / "bin")], env=env, check=True)
    command = directory / "bin" / ("tag.cmd" if os.name == "nt" else "tag")
    subprocess.run([str(command), "version"], cwd=directory, env=env, check=True)
    subprocess.run([str(command), "config", "init", "--json"], cwd=directory, env=env, check=True)
    for key, value in {
        "OPENTAG_BACKEND": "codex",
        "MFS_URL": "http://127.0.0.1:13619",
        "MFS_ALLOWED_SCOPES": "file://local/test",
        "SLACK_ALLOWED_USER_IDS": "UOWNER",
    }.items():
        subprocess.run(
            [str(command), "config", "set", key, value, "--json"],
            cwd=directory,
            env=env,
            check=True,
        )
    subprocess.run([str(command), "doctor", "--offline"], cwd=directory, env=env, check=True)
    current = json.loads((directory / "home/current.json").read_text())
    subprocess.run([current["python"], "-c", "import slack_bolt, psutil, mfs_server"], check=True)
    if os.name != "nt":
        mfs = Path(current["python"]).parent / "mfs"
        subprocess.run([str(mfs), "--version"], check=True)
