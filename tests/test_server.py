"""Unit tests for comfyui-pbr-mcp server, workflow patching, and host configuration."""

from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

REPO_ROOT = Path(__file__).resolve().parents[1]
SRC_DIR = REPO_ROOT / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from comfyui_pbr_mcp import server  # noqa: E402


class _FakeResponse:
    def __init__(self, payload: dict) -> None:
        self._payload = payload

    def raise_for_status(self) -> None:
        return None

    def json(self) -> dict:
        return self._payload


class _FakeHTTP:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str]] = []

    def post(self, url: str, **kwargs):
        self.calls.append(("POST", url))
        if url.endswith("/upload/image"):
            return _FakeResponse({"name": "uploaded_base.png"})
        if url.endswith("/prompt"):
            return _FakeResponse({"prompt_id": "pid-001"})
        raise AssertionError(f"Unexpected POST: {url}")

    def get(self, url: str, **kwargs):
        self.calls.append(("GET", url))
        if url.endswith("/history/pid-001"):
            return _FakeResponse(
                {
                    "pid-001": {
                        "outputs": {
                            "2": {
                                "images": [
                                    {
                                        "filename": "pbr_normal_00001.png",
                                        "subfolder": "pbr",
                                        "type": "output",
                                    }
                                ]
                            }
                        }
                    }
                }
            )
        if url.endswith("/system_stats"):
            return _FakeResponse({"system": {"os": "nt"}})
        raise AssertionError(f"Unexpected GET: {url}")


class _FakeWS:
    def __init__(self) -> None:
        self.closed = False
        self.connected_url = ""
        self._messages = [
            json.dumps({"type": "executing", "data": {"node": "2", "prompt_id": "pid-001"}}),
            json.dumps({"type": "executing", "data": {"node": None, "prompt_id": "pid-001"}}),
        ]

    def settimeout(self, timeout_s: float) -> None:
        self.timeout_s = timeout_s

    def connect(self, url: str) -> None:
        self.connected_url = url

    def recv(self) -> str:
        return self._messages.pop(0)

    def close(self) -> None:
        self.closed = True


class TestComfyUIPBRServer(unittest.TestCase):
    def test_endpoint_resolution_from_env(self) -> None:
        with mock.patch.dict(os.environ, {"COMFYUI_HOST": "10.0.0.25:8188"}, clear=False):
            http_u, ws_u = server.resolve_comfyui_endpoint()
            self.assertEqual(http_u, "http://10.0.0.25:8188")
            self.assertEqual(ws_u, "ws://10.0.0.25:8188")

        http_s, ws_s = server.resolve_comfyui_endpoint("https://render.example.com/")
        self.assertEqual(http_s, "https://render.example.com")
        self.assertEqual(ws_s, "wss://render.example.com")

    def test_bundled_workflow_and_patching(self) -> None:
        wf_path = server.resolve_workflow_file()
        self.assertTrue(wf_path.is_file(), f"Missing bundled workflow: {wf_path}")
        wf = json.loads(wf_path.read_text(encoding="utf-8"))
        patched = server.patch_workflow_image(wf, "input_stone.png")
        self.assertEqual(patched["1"]["inputs"]["image"], "input_stone.png")
        self.assertEqual(wf["1"]["inputs"]["image"], "example.png")

        with self.assertRaises(ValueError):
            server.patch_workflow_image({"1": {"class_type": "KSampler", "inputs": {}}}, "x.png")

    def test_generate_pbr_textures_end_to_end_mocked(self) -> None:
        fake_http = _FakeHTTP()
        fake_ws = _FakeWS()
        fake_ws_mod = SimpleNamespace(WebSocket=lambda: fake_ws)

        with tempfile.TemporaryDirectory() as tmp:
            img_path = Path(tmp) / "albedo.png"
            img_path.write_bytes(b"\x89PNG\r\n\x1a\n")

            with (
                mock.patch.dict(os.environ, {"COMFYUI_HOST": "http://gpu.local:8188"}, clear=False),
                mock.patch.object(server, "_require_http_client", return_value=fake_http),
                mock.patch.object(server, "_require_ws_client", return_value=fake_ws_mod),
            ):
                out = json.loads(server.generate_pbr_textures(str(img_path)))

        self.assertEqual(out["status"], "success")
        self.assertEqual(out["endpoint"], "http://gpu.local:8188")
        self.assertEqual(out["prompt_id"], "pid-001")
        self.assertEqual(len(out["textures"]), 1)
        self.assertIn("filename=pbr_normal_00001.png", out["textures"][0]["url"])
        self.assertTrue(fake_ws.closed)
        self.assertTrue(fake_ws.connected_url.startswith("ws://gpu.local:8188/ws?clientId="))

    def test_missing_image_returns_error_json(self) -> None:
        out = json.loads(server.generate_pbr_textures("/nonexistent/missing.png"))
        self.assertEqual(out["status"], "error")
        self.assertIn("Image not found", out["error"])


if __name__ == "__main__":
    unittest.main()
