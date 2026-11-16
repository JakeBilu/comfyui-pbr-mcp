#!/usr/bin/env python3
"""FastMCP server generating PBR textures from an input image via a ComfyUI API workflow."""

from __future__ import annotations

import json
import os
import urllib.parse
import uuid
from pathlib import Path
from typing import Any

DEFAULT_COMFYUI_HOST = "127.0.0.1:8188"
DEFAULT_WORKFLOW_PATH = Path(__file__).resolve().parent / "workflows" / "workflow_api.json"

try:
    import requests
except ImportError:  # pragma: no cover - optional in stdlib-only unit test run
    requests = None  # type: ignore[assignment]

try:
    import websocket
except ImportError:  # pragma: no cover
    websocket = None  # type: ignore[assignment]

try:
    from mcp.server.fastmcp import FastMCP
except ImportError:  # pragma: no cover
    FastMCP = None  # type: ignore[assignment]


class _NoopMCP:
    def __init__(self, name: str) -> None:
        self.name = name

    def tool(self):
        def _dec(fn):
            return fn

        return _dec

    def run(self) -> None:
        raise RuntimeError(
            "The 'mcp' package is required to run comfyui-pbr-mcp. Install with: pip install mcp"
        )


mcp = FastMCP("comfyui-pbr-mcp") if FastMCP is not None else _NoopMCP("comfyui-pbr-mcp")


def resolve_comfyui_endpoint(host_override: str | None = None) -> tuple[str, str]:
    """Return (http_base_url, ws_base_url) from COMFYUI_HOST env var or default."""
    raw = (host_override or os.environ.get("COMFYUI_HOST", "")).strip() or DEFAULT_COMFYUI_HOST
    if "://" not in raw:
        raw = f"http://{raw}"
    parsed = urllib.parse.urlparse(raw)
    if not parsed.netloc:
        raise ValueError(f"Invalid COMFYUI_HOST value: {raw!r}")
    http_scheme = "https" if parsed.scheme == "https" else "http"
    ws_scheme = "wss" if http_scheme == "https" else "ws"
    base_path = parsed.path.rstrip("/")
    http_base = f"{http_scheme}://{parsed.netloc}{base_path}"
    ws_base = f"{ws_scheme}://{parsed.netloc}{base_path}"
    return http_base, ws_base


def resolve_workflow_file(workflow_path: str | None = None) -> Path:
    """Resolve workflow JSON path via argument, COMFYUI_WORKFLOW_FILE env, or bundled default."""
    candidate = (workflow_path or os.environ.get("COMFYUI_WORKFLOW_FILE", "")).strip()
    return Path(candidate) if candidate else DEFAULT_WORKFLOW_PATH


def _require_http_client() -> Any:
    if requests is None:
        raise RuntimeError("The 'requests' package is required to communicate with ComfyUI.")
    return requests


def _require_ws_client() -> Any:
    if websocket is None:
        raise RuntimeError("The 'websocket-client' package is required to stream ComfyUI progress.")
    return websocket


def upload_image(image_path: Path, http_base: str, timeout_s: float = 30.0) -> str:
    """Upload a local image to ComfyUI and return the server-assigned filename."""
    http = _require_http_client()
    url = f"{http_base}/upload/image"
    with image_path.open("rb") as f:
        response = http.post(
            url,
            files={"image": f},
            data={"overwrite": "true"},
            timeout=timeout_s,
        )
        response.raise_for_status()
        return str(response.json()["name"])


def queue_prompt(
    prompt: dict[str, Any], client_id: str, http_base: str, timeout_s: float = 30.0
) -> str:
    """Queue a ComfyUI API workflow prompt and return its prompt_id."""
    http = _require_http_client()
    url = f"{http_base}/prompt"
    response = http.post(
        url,
        json={"prompt": prompt, "client_id": client_id},
        timeout=timeout_s,
    )
    response.raise_for_status()
    return str(response.json()["prompt_id"])


def get_history(prompt_id: str, http_base: str, timeout_s: float = 30.0) -> dict[str, Any]:
    """Fetch execution history metadata for a prompt_id."""
    http = _require_http_client()
    url = f"{http_base}/history/{prompt_id}"
    response = http.get(url, timeout=timeout_s)
    response.raise_for_status()
    return dict(response.json().get(prompt_id, {}))


def wait_for_completion(ws_conn: Any, prompt_id: str) -> None:
    """Block on ComfyUI WebSocket until execution completes for prompt_id."""
    while True:
        out = ws_conn.recv()
        if not out:
            raise RuntimeError("ComfyUI WebSocket closed before prompt completion.")
        if isinstance(out, str):
            message = json.loads(out)
            if message.get("type") == "executing":
                data = message.get("data", {})
                if data.get("node") is None and data.get("prompt_id") == prompt_id:
                    return


def get_output_paths(history: dict[str, Any], http_base: str) -> list[dict[str, str]]:
    """Extract output image descriptors and view URLs from ComfyUI history."""
    output_images: list[dict[str, str]] = []
    outputs = history.get("outputs", {})
    if isinstance(outputs, dict):
        for node_id, node_output in outputs.items():
            for image in node_output.get("images", []):
                filename = str(image.get("filename", ""))
                subfolder = str(image.get("subfolder", ""))
                folder_type = str(image.get("type", "output"))
                query = urllib.parse.urlencode(
                    {"filename": filename, "subfolder": subfolder, "type": folder_type}
                )
                output_images.append(
                    {
                        "filename": filename,
                        "url": f"{http_base}/view?{query}",
                        "node_id": str(node_id),
                    }
                )
    return output_images


def patch_workflow_image(workflow: dict[str, Any], server_filename: str) -> dict[str, Any]:
    """Inject uploaded server filename into every LoadImage node in a workflow copy."""
    patched = json.loads(json.dumps(workflow))
    modified = False
    for node in patched.values():
        if isinstance(node, dict) and node.get("class_type") == "LoadImage":
            inputs = node.setdefault("inputs", {})
            inputs["image"] = server_filename
            modified = True
    if not modified:
        raise ValueError("No 'LoadImage' node found in ComfyUI workflow JSON.")
    return patched


@mcp.tool()
def check_comfyui_health(comfyui_host: str | None = None) -> str:
    """Verify connectivity to the configured ComfyUI endpoint (/system_stats)."""
    http_base, _ = resolve_comfyui_endpoint(comfyui_host)
    try:
        http = _require_http_client()
        resp = http.get(f"{http_base}/system_stats", timeout=5.0)
        resp.raise_for_status()
        return json.dumps({"ok": True, "endpoint": http_base, "stats": resp.json()}, indent=2)
    except Exception as exc:
        return json.dumps({"ok": False, "endpoint": http_base, "error": str(exc)}, indent=2)


@mcp.tool()
def generate_pbr_textures(
    image_path: str,
    workflow_path: str | None = None,
    comfyui_host: str | None = None,
    timeout_s: float = 120.0,
) -> str:
    """Generate PBR texture maps from a base-color image via ComfyUI API workflow."""
    src_img = Path(image_path)
    if not src_img.is_file():
        return json.dumps({"status": "error", "error": f"Image not found: {image_path}"})

    wf_file = resolve_workflow_file(workflow_path)
    if not wf_file.is_file():
        return json.dumps({"status": "error", "error": f"Workflow file not found: {wf_file}"})

    http_base, ws_base = resolve_comfyui_endpoint(comfyui_host)
    ws_mod = _require_ws_client()
    ws_conn = None
    try:
        server_filename = upload_image(src_img, http_base, timeout_s=min(timeout_s, 30.0))
        workflow = json.loads(wf_file.read_text(encoding="utf-8"))
        patched = patch_workflow_image(workflow, server_filename)

        client_id = str(uuid.uuid4())
        ws_conn = ws_mod.WebSocket()
        ws_conn.settimeout(timeout_s)
        ws_conn.connect(f"{ws_base}/ws?clientId={client_id}")

        prompt_id = queue_prompt(patched, client_id, http_base, timeout_s=min(timeout_s, 30.0))
        wait_for_completion(ws_conn, prompt_id)

        history = get_history(prompt_id, http_base, timeout_s=min(timeout_s, 30.0))
        outputs = get_output_paths(history, http_base)
        return json.dumps(
            {
                "status": "success",
                "endpoint": http_base,
                "prompt_id": prompt_id,
                "message": f"Successfully generated {len(outputs)} textures.",
                "textures": outputs,
            },
            indent=2,
        )
    except Exception as exc:
        return json.dumps(
            {
                "status": "error",
                "endpoint": http_base,
                "error": str(exc),
            },
            indent=2,
        )
    finally:
        if ws_conn is not None:
            try:
                ws_conn.close()
            except Exception:
                pass


def main() -> None:
    mcp.run()


if __name__ == "__main__":
    main()
