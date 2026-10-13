from __future__ import annotations

import json
import os
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest.mock import patch

from scripts import tag_model_download as download

MB = 1_048_576


class ModelDownloadStateTests(unittest.TestCase):
    def setUp(self):
        self._temp = tempfile.TemporaryDirectory()
        self.addCleanup(self._temp.cleanup)
        self.shared = Path(self._temp.name) / "shared/mfs"

    def test_nothing_is_reported_before_a_download_was_started(self):
        self.assertIsNone(download.report(self.shared))

    def test_a_running_download_reports_its_progress_and_what_it_means_for_search(self):
        download.write(self.shared, "downloading", model="bge-m3", done=312 * MB, total=560 * MB, pid=os.getpid())
        report = download.report(self.shared)
        self.assertEqual(report["state"], "downloading")
        self.assertEqual(report["model"], "bge-m3")
        self.assertEqual(report["percent"], 55)
        self.assertEqual(report["message"],
                         "Downloading the search model (bge-m3): 312 of 560 MB. Search is off until it finishes.")

    def test_a_download_whose_process_is_gone_is_reported_as_interrupted(self):
        download.write(self.shared, "downloading", model="bge-m3", done=1, total=2, pid=os.getpid())
        with patch.object(download, "pid_alive", return_value=False):
            report = download.report(self.shared)
        self.assertEqual(report["state"], "failed")
        self.assertIn("interrupted", report["message"])

    def test_a_download_with_a_live_process_stays_downloading(self):
        download.write(self.shared, "downloading", model="bge-m3", done=1, total=2, pid=os.getpid())
        self.assertEqual(download.report(self.shared)["state"], "downloading")

    def test_a_finished_download_is_ready_and_says_nothing_about_search(self):
        download.write(self.shared, "ready", model="bge-m3", done=560 * MB, total=560 * MB)
        report = download.report(self.shared)
        self.assertEqual((report["state"], report["percent"]), ("ready", 100))
        self.assertEqual(report["message"], "")

    def test_a_failure_keeps_the_reason_and_how_to_retry(self):
        download.write(self.shared, "failed", model="bge-m3", error="no network")
        report = download.report(self.shared)
        self.assertIn("no network", report["message"])
        self.assertEqual(report["error"], "no network")
        self.assertIn("tag memory download", report["message"])

    def test_an_unreadable_file_reports_nothing_instead_of_crashing_status(self):
        self.shared.mkdir(parents=True)
        (self.shared / download.NAME).write_text("{not json", encoding="utf-8")
        self.assertIsNone(download.report(self.shared))

    def test_progress_is_written_atomically_and_privately(self):
        download.write(self.shared, "downloading", model="bge-m3", done=1, total=2)
        self.assertEqual(json.loads((self.shared / download.NAME).read_text(encoding="utf-8"))["state"], "downloading")
        self.assertEqual([p.name for p in self.shared.iterdir()], [download.NAME])


class StartBackgroundTests(unittest.TestCase):
    def test_runs_the_script_detached_with_progress_and_marks_the_download_at_once(self):
        with tempfile.TemporaryDirectory() as temp, patch.object(download.subprocess, "Popen") as popen:
            popen.return_value.pid = os.getpid()
            download.start_background("/venv/python", "/release/preload.py", Path(temp), {"PATH": "x"})
            command = popen.call_args.args[0]
            self.assertEqual(command, ["/venv/python", "/release/preload.py", "--progress"])
            self.assertEqual(popen.call_args.kwargs["env"]["TAG_HOME"], temp)
            if os.name != "nt":
                self.assertTrue(popen.call_args.kwargs["start_new_session"])
            self.assertEqual(download.report(Path(temp) / "shared/mfs")["state"], "downloading")


class RecorderTests(unittest.TestCase):
    def setUp(self):
        self._temp = tempfile.TemporaryDirectory()
        self.addCleanup(self._temp.cleanup)
        self.shared = Path(self._temp.name)
        self.now = 0.0
        self.recorder = download.Recorder(self.shared, "bge-m3", clock=lambda: self.now)

    def test_adds_up_every_file_of_the_model(self):
        tokenizer, weights = self.recorder.bar(17 * MB), self.recorder.bar(560 * MB)
        tokenizer.advance(17 * MB)
        self.now = 1
        weights.advance(100 * MB)
        report = download.read(self.shared)
        self.assertEqual((report["done"], report["total"]), (117 * MB, 577 * MB))

    def test_writes_are_throttled_but_the_last_bytes_always_land(self):
        bar = self.recorder.bar(10 * MB)
        bar.advance(1 * MB)  # first write goes out
        bar.advance(1 * MB)  # same instant: held back
        self.assertEqual(download.read(self.shared)["done"], 1 * MB)
        bar.advance(8 * MB)  # completes the file, so it is written at once
        self.assertEqual(download.read(self.shared)["done"], 10 * MB)

    def test_hook_gives_the_huggingface_download_a_bar_that_reports_here(self):
        class Base:
            def __init__(self, *args, total=None, **kwargs):
                self.total, self.n = total, 0

            def update(self, n=1):
                self.n += n

        calls = []
        module = types.ModuleType("huggingface_hub")
        module.hf_hub_download = lambda *args, **kwargs: calls.append(kwargs)
        utils = types.ModuleType("huggingface_hub.utils")
        utils.tqdm = Base
        with patch.dict(sys.modules, {"huggingface_hub": module, "huggingface_hub.utils": utils}):
            self.recorder.hook()
            module.hf_hub_download("repo", "model.onnx", cache_dir="x")
            bar = calls[0]["tqdm_class"](total=5 * MB, desc="model.onnx", name="huggingface_hub.xet_get.transfer")
            bar.update(2 * MB)
        self.assertEqual(calls[0]["cache_dir"], "x")
        self.assertEqual(download.read(self.shared)["done"], 2 * MB)

    def test_the_second_bar_xet_draws_for_the_same_file_is_not_counted_twice(self):
        class Base:
            def __init__(self, *args, total=None, **kwargs):
                self.total = total

            def update(self, n=1):
                pass

        module = types.ModuleType("huggingface_hub")
        module.hf_hub_download = lambda *args, **kwargs: kwargs
        utils = types.ModuleType("huggingface_hub.utils")
        utils.tqdm = Base
        with patch.dict(sys.modules, {"huggingface_hub": module, "huggingface_hub.utils": utils}):
            self.recorder.hook()
            progress = module.hf_hub_download("repo", "model.onnx")["tqdm_class"]
            reassembling = progress(total=5 * MB, name="huggingface_hub.xet_get")
            transfer = progress(total=5 * MB, name="huggingface_hub.xet_get.transfer")
            transfer.update(5 * MB)
            reassembling.update(5 * MB)
        report = download.read(self.shared)
        self.assertEqual((report["done"], report["total"]), (5 * MB, 5 * MB))


if __name__ == "__main__":
    unittest.main()
