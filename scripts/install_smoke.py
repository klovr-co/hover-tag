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

def check_memory_server(python: Path, directory: Path) -> None:
    """Start the installed MFS server and send it the request Tag uses to index a source."""
    import socket
    import time
    import urllib.request

    server = python.parent / ("mfs-server.exe" if os.name == "nt" else "mfs-server")
    assert server.is_file(), f"mfs-server is missing from the Tag runtime: {server}"
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    home = directory / "mfs-home"
    notes = directory / "notes"
    notes.mkdir()
    (notes / "hello.md").write_text("Tag memory smoke test\n", encoding="utf-8")
    log = (directory / "mfs-server.log").open("wb")
    process = subprocess.Popen([str(server), "run", "--bind", f"127.0.0.1:{port}"],
                               env=dict(os.environ, MFS_HOME=str(home)), stdout=log, stderr=log)
    base = f"http://127.0.0.1:{port}"
    try:
        deadline = time.monotonic() + 180
        while True:
            try:
                with urllib.request.urlopen(base + "/healthz", timeout=2) as response:
                    if response.status == 200:
                        break
            except OSError:
                pass
            if process.poll() is not None or time.monotonic() > deadline:
                log.flush()
                raise RuntimeError("mfs-server did not become healthy:\n"
                                   + (directory / "mfs-server.log").read_text(errors="replace")[-3000:])
            time.sleep(1)
        token = (home / "server.token").read_text(encoding="utf-8").strip()
        request = urllib.request.Request(
            base + "/v1/add", method="POST",
            data=json.dumps({"target": str(notes), "full": False, "process": False}).encode(),
            headers={"Content-Type": "application/json", "Authorization": f"Bearer {token}"})
        with urllib.request.urlopen(request, timeout=60) as response:
            assert json.loads(response.read())["job_id"], "MFS did not queue the indexing job"
    finally:
        process.terminate()
        try:
            process.wait(timeout=20)
        except subprocess.TimeoutExpired:
            process.kill()
        log.close()


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
    # The real bootstraps: neither needs a system Python.
    installer = (["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass",
                  "-File", str(source / "install.ps1"), "-BinDir"]
                 if os.name == "nt" else ["sh", str(source / "install.sh"), "--bin-dir"])
    subprocess.run([*installer, str(directory / "bin")], env=env, check=True)
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
    assert current.get("dependency_schema") == 1, current
    # Tag runs on its own Python, never the one that started this script.
    assert Path(current["python"]).resolve() != Path(sys.executable).resolve()
    slack = directory / "home/runtime/slack"
    if not any(slack.rglob("slack.exe" if os.name == "nt" else "slack")):
        import shutil
        assert shutil.which("slack"), "Slack CLI was neither provisioned nor already installed"
    # Memory needs only the MFS server package; Tag talks to it over HTTP.
    assert not (Path(current["python"]).parent / "mfs").exists()
    check_memory_server(Path(current["python"]), directory)
    print("Memory server: starts, answers, and accepts an indexing request")
