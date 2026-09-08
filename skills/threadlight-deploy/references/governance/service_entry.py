"""Adapt serialized ARM environment configuration to the existing service file API."""
import json
import os
from pathlib import Path


def main():
    kind = os.environ["TL_GOV_SERVICE"]
    if kind == "control-plane":
        from govern_control_plane.app import AzureConfiguration, create_app
        from govern_control_plane.models import parse
        environment, destination, schema = "GOV_CONFIG_JSON", "GOV_CONFIG_FILE", AzureConfiguration
    elif kind == "gateway":
        from govern_gateway.server import Configuration, production_app
        from govern_control_plane.models import parse
        environment, destination, schema = "GATEWAY_CONFIG_JSON", "GATEWAY_CONFIG_FILE", Configuration
    else:
        raise ValueError("unknown_governance_service")
    document = parse(schema, os.environ[environment].encode())
    directory = Path("run")
    directory.mkdir(mode=0o700, exist_ok=True)
    path = directory / f"{kind}.json"
    path.write_text(json.dumps(document.model_dump(mode="json")))
    path.chmod(0o600)
    os.environ[destination] = str(path.resolve())
    import uvicorn
    app = create_app() if kind == "control-plane" else production_app()
    uvicorn.run(app, host="0.0.0.0", port=8000, access_log=False, log_level="critical")


if __name__ == "__main__":
    main()
