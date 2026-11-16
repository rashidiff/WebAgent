"""Interactive Windows setup completion and supervised local backend launch."""
import argparse
import json
import os
from pathlib import Path
import shutil
import socket
import subprocess
import sys
import time
from urllib.error import URLError
from urllib.request import ProxyHandler, build_opener

from dotenv import load_dotenv
from pydantic import ValidationError
from backend.settings import Settings

ROOT = Path(__file__).resolve().parent.parent


def configuration_errors(settings):
    provider = settings.llm_provider
    key = getattr(settings, f"{provider}_api_key")
    if provider == "gemini":
        key = key or settings.google_api_key
    errors = []
    if not key.strip() or key.lower().startswith(("your_", "replace_", "changeme")):
        errors.append(f"Set the API key for {provider}.")
    if not settings.llm_model_name or not settings.llm_model_name.strip():
        errors.append("Set LLM_MODEL_NAME.")
    if not 1 <= settings.port <= 65535:
        errors.append("PORT must be between 1 and 65535.")
    if not settings.host.strip():
        errors.append("Set HOST.")
    if (settings.auth_required or settings.host not in {"localhost", "127.0.0.1", "::1"}) and not settings.agent_auth_token.strip():
        errors.append("Set AGENT_AUTH_TOKEN for authenticated or non-localhost use.")
    if settings.auth_required and (not settings.websocket_origins or any("*" in origin for origin in settings.websocket_origins)):
        errors.append("Set WS_ALLOWED_ORIGINS to the exact Chrome extension origin (no wildcards).")
    return errors


def configure():
    env_path = ROOT / ".env"
    if not env_path.exists():
        shutil.copyfile(ROOT / ".env.example", env_path)
    # Reload edits with the same precedence as backend.main. Preserve inherited
    # environment values for variables absent from the file.
    inherited = dict(os.environ)
    while True:
        os.environ.clear()
        os.environ.update(inherited)
        load_dotenv(env_path, override=True)
        try:
            settings = Settings(_env_file=env_path)
            errors = configuration_errors(settings)
        except ValidationError as exc:
            # ValidationError's default representation can include secret values.
            errors = ["Invalid setting: " + ".".join(map(str, e["loc"])) for e in exc.errors()]
        if not errors:
            return settings
        print("Configuration required:\n" + "\n".join(errors))
        subprocess.run(["notepad.exe", str(env_path)], check=True)
        # Modern Notepad may reuse a process and return before its tab closes.
        input("Save .env in Notepad, then press Enter here to validate (Ctrl+C to cancel): ")


def chrome_setup():
    print(f"Chrome: enable Developer mode -> Load unpacked -> {ROOT / 'extension'}")
    print("For an extension already loaded from this folder, use its Reload button after updates.")
    candidates = [shutil.which("chrome")]
    for directory in ("PROGRAMFILES", "PROGRAMFILES(X86)", "LOCALAPPDATA"):
        if os.environ.get(directory):
            candidates.append(str(Path(os.environ[directory]) / "Google/Chrome/Application/chrome.exe"))
    chrome = next((p for p in candidates if p and Path(p).is_file()), None)
    if chrome:
        subprocess.Popen([chrome, "chrome://extensions/"])
    else:
        print("Open chrome://extensions/ in Chrome manually (install Chrome if missing).")


def launch(settings, skip_browser=False):
    # Refuse to reuse an unknown service, even if it returns a similar response.
    family = socket.AF_INET6 if ":" in settings.host else socket.AF_INET
    with socket.socket(family, socket.SOCK_STREAM) as probe:
        try:
            probe.bind((settings.host, settings.port))
        except OSError:
            raise RuntimeError(f"Cannot bind {settings.host}:{settings.port}. Stop the existing server or change HOST/PORT in .env.") from None
    host = settings.host
    if host in {"0.0.0.0", "::"}:
        host = "127.0.0.1" if host == "0.0.0.0" else "::1"
    url = f"http://{'[' + host + ']' if ':' in host else host}:{settings.port}"
    command = [sys.executable, "-m", "uvicorn", "backend.main:app", "--host", settings.host, "--port", str(settings.port)]
    process = subprocess.Popen(command, cwd=ROOT)
    try:
        opener = build_opener(ProxyHandler({}))
        deadline = time.monotonic() + 60
        while time.monotonic() < deadline:
            if process.poll() is not None:
                raise RuntimeError("Backend exited before becoming ready. Review the server error above.")
            try:
                with opener.open(url + "/health", timeout=1) as response:
                    if json.load(response) == {"status": "ok", "service": "webagent"}:
                        break
            except (URLError, OSError, ValueError):
                pass
            time.sleep(0.25)
        else:
            raise RuntimeError("Backend was not ready after 60 seconds. Review the server output.")
        print(f"Backend ready: {url}", flush=True)
        print("If using a custom HOST/PORT, set the matching backend URL in extension Settings.")
        if not skip_browser:
            chrome_setup()
        else:
            print(f"Extension folder: {ROOT / 'extension'}")
        print("Open the extension sidepanel. Press Ctrl+C here to stop the backend.", flush=True)
        code = process.wait()
        if code:
            raise RuntimeError(f"Backend exited with code {code}.")
    finally:
        if process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--skip-browser", action="store_true")
    args = parser.parse_args()
    os.chdir(ROOT)
    try:
        manifest = json.loads((ROOT / "extension/manifest.json").read_text(encoding="utf-8"))
        if manifest.get("manifest_version") != 3:
            raise RuntimeError("Expected the bundled Manifest V3 extension.")
        settings = configure()
        launch(settings, args.skip_browser)
    except KeyboardInterrupt:
        print("\nBackend stopped.")
    except (OSError, RuntimeError, subprocess.SubprocessError, EOFError) as exc:
        print(f"Launch failed: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
