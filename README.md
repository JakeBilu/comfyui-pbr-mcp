# comfyui-pbr-mcp

Lightweight **FastMCP** server that bridges AI agents and local or remote **ComfyUI** instances to generate PBR texture maps (albedo, normal, roughness, AO, height) from a single base-color image.

## Features

- **`COMFYUI_HOST` Environment Support**: Point to `127.0.0.1:8188`, `http://gpu-box:8188`, or an HTTPS reverse proxy cleanly.
- **Custom Workflow Override**: Ships with a minimal `LoadImage` -> `SaveImage` API template (`src/comfyui_pbr_mcp/workflows/workflow_api.json`); override with your full PBR ComfyUI API graph via `COMFYUI_WORKFLOW_FILE` or the `workflow_path` parameter.
- **Safe Resource Cleanup**: Enforces HTTP request timeouts and closes WebSockets cleanly in `finally`.

## Quickstart

```bash
pip install -e .
export COMFYUI_HOST=127.0.0.1:8188
comfyui-pbr-mcp
```

### MCP Client Configuration

```json
{
  "mcpServers": {
    "comfyui-pbr": {
      "command": "comfyui-pbr-mcp",
      "env": {
        "COMFYUI_HOST": "127.0.0.1:8188",
        "COMFYUI_WORKFLOW_FILE": "/path/to/your_pbr_workflow_api.json"
      }
    }
  }
}
```

## Tools

- `check_comfyui_health(comfyui_host=None)`: Checks `/system_stats` reachability.
- `generate_pbr_textures(image_path, workflow_path=None, comfyui_host=None, timeout_s=120.0)`: Uploads the image, injects the uploaded filename into `LoadImage`, streams WebSocket completion, and returns output texture download URLs.

## License

MIT — see [LICENSE](LICENSE).
