"""Generate tarot card images locally and upload them to Google Drive."""

from __future__ import annotations

import argparse
import base64
import getpass
import json
import os
import re
import secrets
import shutil
import subprocess
import sys
import time
import unicodedata
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Optional, Sequence, Tuple
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


VERSION = "0.2.0"
USER_AGENT = f"tarot-cli/{VERSION}"
MODEL = (
    "apolinario/flux-tarot-v1:"
    "6c4ebdf049df552f8c02b3a7bbb3afec3d37b20924282bab8744f1168b6de470"
)
STYLE_TRIGGER = "in the style of TOK a trtcrd tarot style"
REPLICATE_KEYCHAIN_SERVICE = "tarot-cli-replicate"
ANTHROPIC_KEYCHAIN_SERVICE = "tarot-cli-anthropic"
OPENAI_KEYCHAIN_SERVICE = "tarot-cli-openai"
DEFAULT_OUTPUT_DIR = Path.home() / "Pictures" / "Tarot"
PREDICTIONS_URL = "https://api.replicate.com/v1/predictions"
ANTHROPIC_MESSAGES_URL = "https://api.anthropic.com/v1/messages"
OPENAI_RESPONSES_URL = "https://api.openai.com/v1/responses"
OPENAI_IMAGE_EDITS_URL = "https://api.openai.com/v1/images/edits"
CLAUDE_MODEL = "claude-sonnet-5-5"
OPENAI_TEXT_MODEL = "gpt-5.4-mini"
OPENAI_IMAGE_MODEL = "gpt-image-2.5-sunburst"
TEXT_CHECK_MARKER = "__tarot_text_check__"

VISUAL_ELEMENT_SCHEMA = {
    "type": "object",
    "properties": {
        "name": {
            "type": "string",
            "description": "A concrete visual element that can be illustrated.",
        },
        "category": {
            "type": "string",
            "enum": [
                "plant",
                "animal",
                "object",
                "celestial",
                "landscape",
                "material",
            ],
        },
        "symbolic_role": {
            "type": "string",
            "description": "A short explanation of how this element evokes the concept.",
        },
    },
    "required": ["name", "category", "symbolic_role"],
    "additionalProperties": False,
}

SYMBOLIC_DIRECTION_SCHEMA = {
    "type": "object",
    "properties": {
        "elements": {
            "type": "array",
            "items": VISUAL_ELEMENT_SCHEMA,
            "description": "Exactly ten distinct candidate visual elements.",
        },
    },
    "required": ["elements"],
    "additionalProperties": False,
}

CARD_TEXT_CHECK_SCHEMA = {
    "type": "object",
    "properties": {
        "observed_text": {
            "type": "string",
            "description": "The title visibly printed along the bottom of the card.",
        },
        "matches_exactly": {
            "type": "boolean",
            "description": "Whether the bottom title exactly matches the expected text.",
        },
    },
    "required": ["observed_text", "matches_exactly"],
    "additionalProperties": False,
}


def config_path() -> Path:
    """Return the per-user configuration path."""
    config_root = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config"))
    return config_root / "tarot-cli" / "config.json"


def load_config() -> Dict[str, Any]:
    """Load configuration, returning an empty dictionary before setup."""
    path = config_path()
    if not path.exists():
        return {}

    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise SystemExit(f"Could not read {path}: {error}") from error


def save_config(config: Dict[str, Any]) -> None:
    """Save non-secret configuration with user-only permissions."""
    path = config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(config, indent=2) + "\n", encoding="utf-8")
    path.chmod(0o600)


def slugify(words: Sequence[str], max_length: int = 72) -> str:
    """Turn prompt words into a short, filesystem-safe filename component."""
    text = "-".join(words).lower()
    text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode()
    text = re.sub(r"[^a-z0-9]+", "-", text).strip("-")
    text = text[:max_length].rstrip("-")
    return text or "card"


def build_filename(words: Sequence[str], now: Optional[datetime] = None) -> str:
    """Build YYYYMMDD-HHMMSS-prompt-words.png."""
    moment = now or datetime.now().astimezone()
    timestamp = moment.strftime("%Y%m%d-%H%M%S")
    return f"{timestamp}-{slugify(words)}.png"


def build_prompt(
    words: Sequence[str], symbolic_direction: Optional[Dict[str, Any]] = None
) -> str:
    """Turn Claude's symbolic art direction into the Replicate image prompt."""
    user_prompt = " ".join(words).strip()
    if not symbolic_direction:
        return (
            "A person giving a TED talk on a TED stage with the TED logo, "
            f'"{user_prompt}" {STYLE_TRIGGER}. The exact title "{user_prompt}" appears '
            "once, centered along the bottom of the card in clear readable lettering."
        )

    selected = symbolic_direction.get("selected", [])
    symbols = "; ".join(
        f"{element['name']} ({element['category']}: {element['symbolic_role']})"
        for element in selected
    )

    return (
        "A person giving a TED talk on a TED stage with the TED logo, "
        f'"{user_prompt}" {STYLE_TRIGGER}. '
        "Vertical tarot card composition with a decorative border. Integrate exactly "
        f"these three primary visual symbols into the speaker and stage scene: {symbols}. "
        "Arrange the symbols in an unexpected, dreamlike, somewhat abstract but visually "
        "coherent relationship. Render the exact quoted title "
        f'"{user_prompt}" once, centered along the bottom of the card in clear readable '
        "lettering. Keep the TED logo visible on the stage. Do not add any additional "
        "major symbols or any other words, captions, or labels."
    )


def unique_path(directory: Path, filename: str) -> Path:
    """Avoid overwriting an existing local image."""
    candidate = directory / filename
    number = 2
    while candidate.exists():
        candidate = directory / f"{Path(filename).stem}-{number}.png"
        number += 1
    return candidate


def keychain_account() -> str:
    return getpass.getuser()


def save_secret_to_keychain(secret: str, service: str, label: str) -> None:
    """Store an API secret in the current user's macOS Keychain."""
    if not shutil.which("security"):
        raise SystemExit("macOS Keychain command 'security' was not found.")

    result = subprocess.run(
        [
            "security",
            "add-generic-password",
            "-U",
            "-a",
            keychain_account(),
            "-s",
            service,
            "-w",
            secret,
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        message = result.stderr.strip() or "unknown Keychain error"
        raise SystemExit(f"Could not save the {label}: {message}")


def read_secret(environment_name: str, service: str, label: str) -> str:
    """Read one API secret from the environment, then macOS Keychain."""
    environment_token = os.environ.get(environment_name, "").strip()
    if environment_token:
        return environment_token

    if shutil.which("security"):
        result = subprocess.run(
            [
                "security",
                "find-generic-password",
                "-a",
                keychain_account(),
                "-s",
                service,
                "-w",
            ],
            capture_output=True,
            text=True,
            check=False,
        )
        if result.returncode == 0 and result.stdout.strip():
            return result.stdout.strip()

    raise SystemExit(f"No {label} found. Run: tarot setup")


def read_replicate_token() -> str:
    return read_secret(
        "REPLICATE_API_TOKEN",
        REPLICATE_KEYCHAIN_SERVICE,
        "Replicate API token",
    )


def read_anthropic_key() -> str:
    return read_secret(
        "ANTHROPIC_API_KEY",
        ANTHROPIC_KEYCHAIN_SERVICE,
        "Anthropic API key",
    )


def read_openai_key() -> str:
    return read_secret(
        "OPENAI_API_KEY",
        OPENAI_KEYCHAIN_SERVICE,
        "OpenAI API key for --text",
    )


def anthropic_workspace_id() -> str:
    """Return the workspace selected for an organization-scoped Claude key."""
    return (
        os.environ.get("ANTHROPIC_WORKSPACE_ID", "").strip()
        or str(load_config().get("anthropic_workspace_id", "")).strip()
    )


def setup() -> None:
    """Configure API secrets, Drive destination, and local output folder."""
    print("Tarot CLI setup")
    replicate_token = getpass.getpass(
        "Replicate API token (hidden; leave blank to keep existing): "
    ).strip()
    if replicate_token:
        save_secret_to_keychain(
            replicate_token,
            REPLICATE_KEYCHAIN_SERVICE,
            "Replicate API token",
        )
    else:
        read_replicate_token()

    anthropic_key = getpass.getpass(
        "Anthropic API key (hidden; leave blank to keep existing): "
    ).strip()
    if anthropic_key:
        save_secret_to_keychain(
            anthropic_key,
            ANTHROPIC_KEYCHAIN_SERVICE,
            "Anthropic API key",
        )
    else:
        read_anthropic_key()

    existing_openai_key = False
    try:
        read_openai_key()
        existing_openai_key = True
    except SystemExit:
        pass
    openai_key = getpass.getpass(
        "OpenAI API key (optional, for --text; hidden; leave blank to keep/skip): "
    ).strip()
    if openai_key:
        save_secret_to_keychain(
            openai_key,
            OPENAI_KEYCHAIN_SERVICE,
            "OpenAI API key",
        )
    elif existing_openai_key:
        print("Keeping the existing OpenAI API key.")

    existing_config = load_config()
    existing_workspace = existing_config.get("anthropic_workspace_id", "")
    workspace_prompt = (
        "Anthropic workspace ID (wrkspc_...; blank for a workspace-scoped key)"
    )
    if existing_workspace:
        workspace_prompt += f" [{existing_workspace}]"
    workspace_id = input(f"{workspace_prompt}: ").strip() or existing_workspace
    if workspace_id and not workspace_id.startswith("wrkspc_"):
        raise SystemExit("Anthropic workspace IDs must begin with 'wrkspc_'.")

    existing_folder = existing_config.get("drive_folder_id", "")
    folder_prompt = "Google Drive destination folder ID"
    if existing_folder:
        folder_prompt += f" [{existing_folder}]"
    folder_id = input(f"{folder_prompt}: ").strip() or existing_folder
    if not folder_id:
        raise SystemExit("No Drive folder ID entered; setup canceled.")

    default_output = existing_config.get("output_dir", str(DEFAULT_OUTPUT_DIR))
    output_answer = input(f"Local output folder [{default_output}]: ").strip()
    output_dir = str(Path(output_answer).expanduser()) if output_answer else default_output

    save_config(
        {
            "anthropic_workspace_id": workspace_id,
            "drive_folder_id": folder_id,
            "output_dir": output_dir,
        }
    )
    print(f"Saved configuration to {config_path()}")
    print("API secrets are stored in macOS Keychain.")


def set_anthropic_workspace(workspace_id: str) -> None:
    """Save the workspace used by an organization-scoped Anthropic key."""
    if not workspace_id.startswith("wrkspc_"):
        raise SystemExit("Anthropic workspace IDs must begin with 'wrkspc_'.")
    config = load_config()
    config["anthropic_workspace_id"] = workspace_id
    save_config(config)
    print(f"Anthropic workspace set to {workspace_id}")


def gws_executable() -> Optional[str]:
    """Find a system-installed or project-local Google Workspace CLI."""
    system_gws = shutil.which("gws")
    if system_gws:
        return system_gws

    project_root = Path(__file__).resolve().parents[2]
    bundled_gws = project_root / "tools" / "gws"
    if bundled_gws.is_file() and os.access(bundled_gws, os.X_OK):
        return str(bundled_gws)
    return None


def google_auth() -> None:
    """Run the Google Workspace CLI's interactive setup and login."""
    gws = gws_executable()
    if not gws:
        raise SystemExit("Google Workspace CLI was not found.")

    if shutil.which("gcloud"):
        setup_result = subprocess.run([gws, "auth", "setup"], check=False)
        if setup_result.returncode != 0:
            raise SystemExit(setup_result.returncode)
    else:
        client_secret = Path.home() / ".config" / "gws" / "client_secret.json"
        if not client_secret.exists():
            raise SystemExit(
                "Google OAuth credentials are not configured. Download a Desktop app "
                f"OAuth client JSON to {client_secret}, then run 'tarot google-auth' again. "
                "See the Tarot CLI README for the exact steps."
            )

    login_result = subprocess.run(
        [gws, "auth", "login", "--services", "drive"],
        check=False,
    )
    if login_result.returncode != 0:
        raise SystemExit(login_result.returncode)


def anthropic_request(request: Request, timeout: int = 75) -> Dict[str, Any]:
    """Send one Claude Messages API request and return its JSON response."""
    try:
        with urlopen(request, timeout=timeout) as response:
            return json.loads(response.read().decode("utf-8"))
    except HTTPError as error:
        detail = error.read().decode("utf-8", errors="replace")
        if "anthropic-workspace-id" in detail:
            raise SystemExit(
                "This Anthropic key is organization-scoped and needs a workspace ID. "
                "Find the wrkspc_... value in Claude Console → Settings → Workspaces, "
                "then run: tarot set-workspace WRKSPC_ID"
            ) from error
        raise SystemExit(f"Claude API error ({error.code}): {detail}") from error
    except URLError as error:
        raise SystemExit(f"Could not connect to Claude: {error.reason}") from error
    except json.JSONDecodeError as error:
        raise SystemExit("Claude returned an unreadable response.") from error


def choose_three_elements(
    elements: Sequence[Dict[str, str]], random_source: Optional[Any] = None
) -> Sequence[Dict[str, str]]:
    """Randomly choose three distinct visual elements without replacement."""
    if len(elements) != 10:
        raise SystemExit(
            f"Claude returned {len(elements)} visual elements instead of exactly 10."
        )
    chooser = random_source or secrets.SystemRandom()
    return chooser.sample(list(elements), 3)


def parse_json_object(text: str) -> Dict[str, Any]:
    """Parse a JSON object, tolerating an accidental Markdown code fence."""
    candidates = [text.strip()]
    candidates.extend(
        match.group(1).strip()
        for match in re.finditer(
            r"```(?:json)?\s*(.*?)\s*```",
            text,
            flags=re.IGNORECASE | re.DOTALL,
        )
    )

    decoder = json.JSONDecoder()
    for candidate in candidates:
        try:
            parsed = json.loads(candidate)
        except json.JSONDecodeError:
            opening_brace = candidate.find("{")
            if opening_brace < 0:
                continue
            try:
                parsed, _ = decoder.raw_decode(candidate[opening_brace:])
            except json.JSONDecodeError:
                continue
        if isinstance(parsed, dict):
            return parsed

    raise ValueError("No complete JSON object was found.")


def validate_visual_elements(generated: Dict[str, Any]) -> Sequence[Dict[str, str]]:
    """Validate Claude's ten visual-element objects before sampling them."""
    elements = generated.get("elements")
    if not isinstance(elements, list):
        raise SystemExit("Claude's symbolic direction did not contain an elements list.")

    required_fields = ("name", "category", "symbolic_role")
    for position, element in enumerate(elements, start=1):
        if not isinstance(element, dict) or any(
            not isinstance(element.get(field), str) or not element[field].strip()
            for field in required_fields
        ):
            raise SystemExit(
                f"Claude returned an invalid visual element at position {position}."
            )
    return elements


def plan_card(card_word: str) -> Dict[str, Any]:
    """Ask Claude for ten visual elements, then randomly choose three."""
    variation_key = secrets.randbelow(1_000_000)
    prompt_data = {
        "card_word": card_word,
        "variation_key": variation_key,
        "request": (
            "Generate exactly ten distinct candidate visual elements for a tarot card. "
            "Each must be surprising but semantically resonant rather than a literal "
            "illustration of the word."
        ),
    }
    body = {
        "model": CLAUDE_MODEL,
        "max_tokens": 2048,
        "system": (
            "You are an imaginative tarot symbol researcher. Generate exactly ten unique, "
            "concrete visual elements for the supplied concept. Make the pool varied across "
            "plants, animals, objects, celestial motifs, landscapes, and materials; include "
            "at least two plants and two animals. Let the variation key push the candidates "
            "toward a different valid interpretation each time. Favor dream logic and "
            "visual metaphor. Each element must work in combination with any two others. "
            "Do not create a full composition and do not request lettering or readable text."
        ),
        "messages": [
            {
                "role": "user",
                "content": json.dumps(prompt_data),
            }
        ],
        "output_config": {
            "effort": "low",
            "format": {
                "type": "json_schema",
                "schema": SYMBOLIC_DIRECTION_SCHEMA,
            },
        },
    }
    headers = {
        "x-api-key": read_anthropic_key(),
        "anthropic-version": "2023-06-01",
        "content-type": "application/json",
        "user-agent": USER_AGENT,
    }
    workspace_id = anthropic_workspace_id()
    if workspace_id:
        headers["anthropic-workspace-id"] = workspace_id

    request = Request(
        ANTHROPIC_MESSAGES_URL,
        data=json.dumps(body).encode("utf-8"),
        method="POST",
        headers=headers,
    )
    response = anthropic_request(request)
    text_blocks = [
        block.get("text", "")
        for block in response.get("content", [])
        if block.get("type") == "text"
    ]
    if not text_blocks:
        raise SystemExit("Claude did not return a symbolic card direction.")

    if response.get("stop_reason") == "max_tokens":
        raise SystemExit(
            "Claude's symbolic direction was cut off at the output-token limit. "
            "Run the command again."
        )

    try:
        generated = parse_json_object("".join(text_blocks))
    except ValueError as error:
        stop_reason = response.get("stop_reason", "unknown")
        raise SystemExit(
            "Claude returned symbolic direction that was not valid JSON "
            f"(stop reason: {stop_reason}). Run the command again."
        ) from error

    candidates = validate_visual_elements(generated)
    selected = choose_three_elements(candidates)
    print("Claude candidates: " + ", ".join(item["name"] for item in candidates))
    print("Randomly selected: " + ", ".join(item["name"] for item in selected))
    return {"candidates": candidates, "selected": selected}


def replicate_request(request: Request, timeout: int = 75) -> Dict[str, Any]:
    """Send one Replicate API request and return its JSON response."""
    try:
        with urlopen(request, timeout=timeout) as response:
            return json.loads(response.read().decode("utf-8"))
    except HTTPError as error:
        detail = error.read().decode("utf-8", errors="replace")
        raise SystemExit(f"Replicate API error ({error.code}): {detail}") from error
    except URLError as error:
        raise SystemExit(f"Could not connect to Replicate: {error.reason}") from error
    except json.JSONDecodeError as error:
        raise SystemExit("Replicate returned an unreadable response.") from error


def wait_for_prediction(prediction: Dict[str, Any], token: str) -> Dict[str, Any]:
    """Poll a prediction if it did not finish during the synchronous request."""
    terminal_states = {"succeeded", "failed", "canceled"}
    while prediction.get("status") not in terminal_states:
        status = prediction.get("status", "starting")
        print(f"Replicate status: {status}")
        time.sleep(2)
        prediction_url = prediction.get("urls", {}).get("get")
        if not prediction_url:
            raise SystemExit("Replicate did not return a prediction status URL.")
        request = Request(
            prediction_url,
            headers={"Authorization": f"Bearer {token}"},
        )
        prediction = replicate_request(request)
    return prediction


def download_image(image_url: str, output_path: Path) -> None:
    """Download an HTTPS or data-URL image to a local path."""
    output_path.parent.mkdir(parents=True, exist_ok=True)
    if image_url.startswith("data:"):
        try:
            encoded = image_url.split(",", 1)[1]
            output_path.write_bytes(base64.b64decode(encoded))
            return
        except (IndexError, ValueError) as error:
            raise SystemExit("Replicate returned an invalid image data URL.") from error

    try:
        request = Request(image_url, headers={"User-Agent": USER_AGENT})
        with urlopen(request, timeout=75) as response:
            with output_path.open("wb") as destination:
                shutil.copyfileobj(response, destination)
    except (HTTPError, URLError, OSError) as error:
        raise SystemExit(f"Could not save the generated image: {error}") from error


def generate_image(
    prompt_words: Sequence[str],
    output_path: Path,
    seed: Optional[int],
    symbolic_direction: Optional[Dict[str, Any]] = None,
) -> None:
    """Run the Replicate model over HTTP and save the first returned image."""
    token = read_replicate_token()
    model_input: Dict[str, Any] = {
        "prompt": build_prompt(prompt_words, symbolic_direction),
        "aspect_ratio": "2:3",
        "output_format": "png",
        "num_outputs": 1,
        "num_inference_steps": 28,
        "guidance_scale": 3,
        "lora_scale": 1,
    }
    if seed is not None:
        model_input["seed"] = seed

    print(f"Generating: {' '.join(prompt_words)}")
    body = json.dumps({"version": MODEL, "input": model_input}).encode("utf-8")
    request = Request(
        PREDICTIONS_URL,
        data=body,
        method="POST",
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
            "Prefer": "wait=60",
            "User-Agent": USER_AGENT,
        },
    )
    prediction = wait_for_prediction(replicate_request(request), token)
    if prediction.get("status") != "succeeded":
        detail = prediction.get("error") or prediction.get("logs") or "unknown error"
        raise SystemExit(f"Replicate prediction {prediction.get('status')}: {detail}")

    outputs = prediction.get("output")
    if not outputs:
        raise SystemExit("Replicate completed without returning an image.")
    image_url = outputs[0] if isinstance(outputs, list) else outputs

    download_image(str(image_url), output_path)
    print(f"Saved locally: {output_path}")


def upload_to_drive(image_path: Path, folder_id: str) -> None:
    """Upload an image with the official Google Workspace CLI."""
    gws = gws_executable()
    if not gws:
        raise SystemExit("Google Workspace CLI was not found.")
    command = [
        gws,
        "drive",
        "+upload",
        str(image_path),
        "--parent",
        folder_id,
        "--name",
        image_path.name,
    ]
    result = subprocess.run(command, capture_output=True, text=True, check=False)
    if result.returncode != 0:
        detail = result.stderr.strip() or result.stdout.strip() or "unknown gws error"
        raise SystemExit(
            f"Image was generated and kept locally, but Drive upload failed:\n{detail}"
        )
    print(f"Uploaded to Google Drive: {image_path.name}")


def openai_request(request: Request, timeout: int = 180) -> Dict[str, Any]:
    """Send one OpenAI API request and return its JSON response."""
    try:
        with urlopen(request, timeout=timeout) as response:
            return json.loads(response.read().decode("utf-8"))
    except HTTPError as error:
        detail = error.read().decode("utf-8", errors="replace")
        if error.code == 401:
            raise SystemExit(
                "OpenAI rejected the API key. Run 'tarot setup' and save a valid key."
            ) from error
        raise SystemExit(f"OpenAI API error ({error.code}): {detail}") from error
    except URLError as error:
        raise SystemExit(f"Could not connect to OpenAI: {error.reason}") from error
    except json.JSONDecodeError as error:
        raise SystemExit("OpenAI returned an unreadable response.") from error


def openai_output_text(response: Dict[str, Any]) -> str:
    """Extract concatenated assistant text from a Responses API response."""
    direct_text = response.get("output_text")
    if isinstance(direct_text, str) and direct_text.strip():
        return direct_text

    text_parts = []
    for item in response.get("output", []):
        if item.get("type") != "message":
            continue
        for content in item.get("content", []):
            if content.get("type") == "output_text":
                text_parts.append(content.get("text", ""))
            elif content.get("type") == "refusal":
                raise SystemExit(
                    "OpenAI declined to inspect the card: "
                    + content.get("refusal", "unknown reason")
                )
    if not text_parts:
        raise SystemExit("OpenAI did not return a card-text inspection result.")
    return "".join(text_parts)


def inspect_card_text(
    image_path: Path, expected_text: str, openai_key: str
) -> Dict[str, Any]:
    """Read the bottom title with OpenAI vision and compare it to the term."""
    try:
        encoded_image = base64.b64encode(image_path.read_bytes()).decode("ascii")
    except OSError as error:
        raise SystemExit(f"Could not read {image_path} for text inspection: {error}") from error

    expected_json = json.dumps(expected_text, ensure_ascii=False)
    prompt = (
        "Inspect only the title lettering centered along the bottom of this tarot card. "
        "Ignore the TED logo and any lettering elsewhere in the artwork. The expected "
        f"bottom title is {expected_json}. Transcribe the visible bottom title into "
        "observed_text. Set matches_exactly to true only when spelling, spaces, "
        "punctuation, and capitalization exactly match the expected title. If the title "
        "is absent or unreadable, use an empty observed_text and false."
    )
    body = {
        "model": OPENAI_TEXT_MODEL,
        "input": [
            {
                "role": "user",
                "content": [
                    {"type": "input_text", "text": prompt},
                    {
                        "type": "input_image",
                        "image_url": f"data:image/png;base64,{encoded_image}",
                        "detail": "high",
                    },
                ],
            }
        ],
        "text": {
            "format": {
                "type": "json_schema",
                "name": "card_text_check",
                "strict": True,
                "schema": CARD_TEXT_CHECK_SCHEMA,
            }
        },
        "max_output_tokens": 200,
    }
    request = Request(
        OPENAI_RESPONSES_URL,
        data=json.dumps(body).encode("utf-8"),
        method="POST",
        headers={
            "Authorization": f"Bearer {openai_key}",
            "Content-Type": "application/json",
            "User-Agent": USER_AGENT,
        },
    )
    response = openai_request(request)
    try:
        result = json.loads(openai_output_text(response))
    except json.JSONDecodeError as error:
        raise SystemExit("OpenAI returned an invalid card-text inspection result.") from error
    if not isinstance(result.get("observed_text"), str) or not isinstance(
        result.get("matches_exactly"), bool
    ):
        raise SystemExit("OpenAI returned an incomplete card-text inspection result.")
    result["matches_exactly"] = bool(
        result["matches_exactly"] and result["observed_text"] == expected_text
    )
    return result


def multipart_image_request(
    fields: Dict[str, str], image_path: Path
) -> Tuple[bytes, str]:
    """Build a multipart form body for one OpenAI image edit."""
    boundary = f"tarot-{secrets.token_hex(16)}"
    body = bytearray()
    for name, value in fields.items():
        body.extend(f"--{boundary}\r\n".encode("ascii"))
        body.extend(
            f'Content-Disposition: form-data; name="{name}"\r\n\r\n'.encode(
                "ascii"
            )
        )
        body.extend(value.encode("utf-8"))
        body.extend(b"\r\n")

    filename = image_path.name.replace('"', "")
    body.extend(f"--{boundary}\r\n".encode("ascii"))
    body.extend(
        (
            'Content-Disposition: form-data; name="image[]"; '
            f'filename="{filename}"\r\n'
        ).encode("utf-8")
    )
    body.extend(b"Content-Type: image/png\r\n\r\n")
    body.extend(image_path.read_bytes())
    body.extend(b"\r\n")
    body.extend(f"--{boundary}--\r\n".encode("ascii"))
    return bytes(body), f"multipart/form-data; boundary={boundary}"


def edit_card_text(image_path: Path, expected_text: str, openai_key: str) -> bytes:
    """Ask GPT Image to preserve a card while repairing only its bottom title."""
    expected_json = json.dumps(expected_text, ensure_ascii=False)
    prompt = (
        "Edit this exact existing vertical tarot card. Preserve its speaker, TED stage, "
        "TED logo, border, colors, symbols, composition, and illustration style. Change "
        "only the title lettering centered along the bottom. Remove any incorrect, "
        "garbled, missing, or duplicate bottom title and replace it with exactly "
        f"{expected_json}, with identical capitalization and punctuation. Make the title "
        "clear and readable. Do not add any other text and do not redesign the card."
    )
    fields = {
        "model": OPENAI_IMAGE_MODEL,
        "prompt": prompt,
        "quality": "high",
        "size": "1024x1536",
        "output_format": "png",
    }
    try:
        body, content_type = multipart_image_request(fields, image_path)
    except OSError as error:
        raise SystemExit(f"Could not read {image_path} for text correction: {error}") from error
    request = Request(
        OPENAI_IMAGE_EDITS_URL,
        data=body,
        method="POST",
        headers={
            "Authorization": f"Bearer {openai_key}",
            "Content-Type": content_type,
            "User-Agent": USER_AGENT,
        },
    )
    response = openai_request(request)
    try:
        encoded_image = response["data"][0]["b64_json"]
        return base64.b64decode(encoded_image, validate=True)
    except (KeyError, IndexError, TypeError, ValueError) as error:
        raise SystemExit("OpenAI completed without returning a corrected PNG.") from error


def ensure_card_text(image_path: Path, expected_text: str, openai_key: str) -> None:
    """Verify a title; edit and verify again only when it is incorrect."""
    print(f'OpenAI is checking the bottom title against "{expected_text}"...')
    first_check = inspect_card_text(image_path, expected_text, openai_key)
    observed = first_check["observed_text"] or "(missing or unreadable)"
    if first_check["matches_exactly"]:
        print(f'OpenAI verified the title: "{expected_text}"')
        return

    print(f'OpenAI read "{observed}"; correcting the bottom title...')
    corrected_bytes = edit_card_text(image_path, expected_text, openai_key)
    temporary_path = image_path.with_name(
        f".{image_path.stem}-openai-{secrets.token_hex(6)}.png"
    )
    try:
        temporary_path.write_bytes(corrected_bytes)
        second_check = inspect_card_text(temporary_path, expected_text, openai_key)
        if not second_check["matches_exactly"]:
            second_observed = second_check["observed_text"] or "missing or unreadable"
            raise SystemExit(
                "OpenAI edited the card, but could not verify the exact title "
                f'afterward (read: "{second_observed}"). The original local image was '
                "kept and nothing was uploaded."
            )
        temporary_path.replace(image_path)
    finally:
        if temporary_path.exists():
            temporary_path.unlink()
    print(f'OpenAI corrected and verified the title: "{expected_text}"')


def doctor() -> None:
    """Report whether the local dependencies and configuration are ready."""
    config = load_config()
    checks = {
        "gws installed": bool(gws_executable()),
        "Drive folder configured": bool(config.get("drive_folder_id")),
        "Replicate token available": False,
        "Anthropic key available": False,
    }
    try:
        read_replicate_token()
        checks["Replicate token available"] = True
    except SystemExit:
        pass
    try:
        read_anthropic_key()
        checks["Anthropic key available"] = True
    except SystemExit:
        pass

    openai_available = False
    try:
        read_openai_key()
        openai_available = True
    except SystemExit:
        pass

    for label, passed in checks.items():
        print(f"{'OK' if passed else 'MISSING':7} {label}")
    print(
        f"{'OK' if openai_available else 'OPTIONAL':7} "
        "OpenAI key available for --text"
    )
    if not all(checks.values()):
        raise SystemExit(1)


def expand_card_requests(
    terms_and_counts: Sequence[str],
) -> Sequence[Tuple[str, int, int, bool]]:
    """Expand term modifiers into (term, copy number, total, text check)."""
    requests = []
    position = 0
    while position < len(terms_and_counts):
        term = terms_and_counts[position]
        if term.isdecimal() or term in {"--text", TEXT_CHECK_MARKER}:
            raise SystemExit(
                f"Modifier '{term}' must immediately follow a card term."
            )

        copies = 1
        count_seen = False
        check_text = False
        position += 1
        while position < len(terms_and_counts):
            modifier = terms_and_counts[position]
            if modifier in {"--text", TEXT_CHECK_MARKER}:
                if check_text:
                    raise SystemExit(f"--text was repeated for '{term}'.")
                check_text = True
                position += 1
                continue
            if modifier.isdecimal():
                if count_seen:
                    raise SystemExit(f"Multiple copy counts were provided for '{term}'.")
                copies = int(modifier)
                if not 1 <= copies <= 5:
                    raise SystemExit(
                        f"Copy count for '{term}' must be between 1 and 5."
                    )
                count_seen = True
                position += 1
                continue
            break

        requests.extend(
            (term, copy_number, copies, check_text)
            for copy_number in range(1, copies + 1)
        )

    return requests


def parser() -> argparse.ArgumentParser:
    command = argparse.ArgumentParser(
        prog="tarot",
        description=(
            "Generate tarot images and upload them to Google Drive. "
            "Optionally follow each term with --text and/or a copy count from 1 to 5."
        ),
    )
    command.add_argument(
        "words",
        nargs="*",
        metavar="TERM_OR_COUNT",
        help=(
            "Each word or quoted phrase becomes a card; an immediately following "
            "number from 1 to 5 sets its copy count"
        ),
    )
    command.add_argument("--seed", type=int, help="Optional repeatable generation seed")
    command.add_argument(
        "--text",
        action="store_true",
        help=(
            "For the immediately preceding term and all its copies, have OpenAI "
            "verify and, only if needed, correct the bottom title"
        ),
    )
    command.add_argument(
        "--local-only",
        action="store_true",
        help="Generate the image without uploading it",
    )
    return command


def main(argv: Optional[Sequence[str]] = None) -> None:
    arguments = list(argv if argv is not None else sys.argv[1:])
    if arguments == ["setup"]:
        setup()
        return
    if arguments == ["doctor"]:
        doctor()
        return
    if arguments == ["google-auth"]:
        google_auth()
        return
    if arguments[:1] == ["set-workspace"]:
        if len(arguments) != 2:
            raise SystemExit("Usage: tarot set-workspace wrkspc_...")
        set_anthropic_workspace(arguments[1])
        return

    parser_arguments = [
        TEXT_CHECK_MARKER if argument == "--text" else argument
        for argument in arguments
    ]
    args = parser().parse_args(parser_arguments)
    if not args.words:
        parser().print_help()
        raise SystemExit(2)

    config = load_config()
    output_dir = Path(config.get("output_dir", DEFAULT_OUTPUT_DIR)).expanduser()
    folder_id = os.environ.get("TAROT_DRIVE_FOLDER_ID") or config.get(
        "drive_folder_id"
    )
    if not args.local_only and not folder_id:
        raise SystemExit("No Drive folder is configured. Run: tarot setup")

    requests = expand_card_requests(args.words)
    openai_key = read_openai_key() if any(request[3] for request in requests) else ""
    batch_time = datetime.now().astimezone()
    total = len(requests)
    for index, (word, copy_number, copy_total, check_text) in enumerate(
        requests, start=1
    ):
        copy_label = (
            f" (copy {copy_number} of {copy_total})" if copy_total > 1 else ""
        )
        text_label = " [OpenAI text check]" if check_text else ""
        print(f"\nCard {index} of {total}: {word}{copy_label}{text_label}")
        filename = build_filename([word], now=batch_time)
        output_path = unique_path(output_dir, filename)
        symbolic_direction = plan_card(word)
        generate_image([word], output_path, args.seed, symbolic_direction)
        if check_text:
            ensure_card_text(output_path, word, openai_key)

        if not args.local_only:
            upload_to_drive(output_path, str(folder_id))


if __name__ == "__main__":
    main()
