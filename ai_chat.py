#!/usr/bin/env python3
"""
ai_chat.py

Ask questions about a finished transcript using an AI model.

Three providers are supported:
    claude  - Anthropic's Claude (needs an API key, sends the transcript online)
    openai  - OpenAI or any OpenAI-compatible service (needs an API key)
    ollama  - a model running on this computer through Ollama (no key, offline)

Uses only the Python standard library. Settings (including API keys) are kept
in a private file in the user's home folder and never leave this computer
except as the credential for the provider they belong to.
"""

from __future__ import annotations

import copy
import json
import re
import os
import socket
import tempfile
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Optional

SETTINGS_PATH = Path.home() / ".skimwatch" / "settings.json"
# Where settings lived before the app was renamed from Local Transcribe (1.3).
LEGACY_SETTINGS_DIR = Path.home() / ".local-transcribe"


def migrate_legacy_settings() -> None:
    """Move ~/.local-transcribe to ~/.skimwatch once, so nobody loses a key."""
    new_dir = SETTINGS_PATH.parent
    if new_dir.exists() or not LEGACY_SETTINGS_DIR.is_dir():
        return
    try:
        LEGACY_SETTINGS_DIR.rename(new_dir)
    except OSError:
        pass

ASK_TIMEOUT = 120  # seconds; long transcripts and local models can be slow
MODELS_TIMEOUT = 15
MAX_MESSAGES = 60
MAX_MESSAGE_CHARS = 20_000
MAX_MODEL_CHARS = 200

ANTHROPIC_VERSION = "2023-06-01"
# Non-streaming request, so keep the answer budget well inside the timeout.
CLAUDE_MAX_TOKENS = 16000
# If Claude's safety filters decline a question, let Anthropic retry it on its
# recommended substitute model instead of failing. Only these models accept it.
CLAUDE_FALLBACK_BETA = "server-side-fallback-2026-07-01"
CLAUDE_FALLBACK_MODELS = {
    "claude-opus-5-5", "claude-opus-5", "claude-sonnet-5-5",
    "claude-fable-5-1", "claude-fable-5",
}
CLAUDE_MODELS = ["claude-opus-5-5", "claude-sonnet-5-5", "claude-haiku-5-5", "claude-fable-5-1"]

# Ollama only reads the first few thousand tokens unless told otherwise, which
# would silently cut off most transcripts.
OLLAMA_MIN_CONTEXT = 8192
OLLAMA_MAX_CONTEXT = 32768

PROVIDERS: dict = {
    "claude": {
        "name": "Claude (Anthropic)",
        "needs_key": True,
        "default_model": "claude-opus-5-5",
        "default_base_url": "https://api.anthropic.com",
        "env_key": "ANTHROPIC_API_KEY",
        "sends_data_off_device": True,
    },
    "openai": {
        "name": "OpenAI (or compatible)",
        "needs_key": True,
        "default_model": "gpt-4o",
        "default_base_url": "https://api.openai.com",
        "env_key": "OPENAI_API_KEY",
        "sends_data_off_device": True,
    },
    "ollama": {
        "name": "Ollama (on this computer)",
        "needs_key": False,
        "default_model": "llama3.2",
        "default_base_url": "http://127.0.0.1:11434",
        "env_key": "",
        "sends_data_off_device": False,
    },
}
PROVIDER_IDS = list(PROVIDERS)

# Model ids from an OpenAI-style list that are clearly not for chatting.
_NOT_CHAT = (
    "embedding", "embed", "whisper", "tts", "dall-e", "image", "audio", "realtime",
    "moderation", "transcribe", "instruct", "davinci", "babbage", "sora", "rerank",
)

INSTRUCTIONS = (
    "You are helping someone understand a video by answering questions about its "
    "transcript. Answer using the transcript. If the transcript does not contain "
    "the answer, say so plainly instead of guessing. The transcript was produced "
    "automatically by speech recognition, so it may contain mistakes such as "
    "misheard words or names; mention this when it affects your answer. Reply in "
    "plain text without Markdown formatting, in the same language as the question."
)


class AIError(Exception):
    """A problem whose message is safe and friendly enough to show the user."""


# ---------------------------------------------------------------------------
# Settings
# ---------------------------------------------------------------------------

def default_settings() -> dict:
    return {
        "provider": "",
        # Family sharing: may visitors ask questions (billed to the owner's key)?
        "visitors_can_ask": False,
        "providers": {
            pid: {"api_key": "", "model": "", "base_url": "", "workspace_id": ""}
            for pid in PROVIDER_IDS
        },
    }


def _normalise(raw: object) -> dict:
    """Copy only the parts we understand, so a damaged file cannot break us."""
    settings = default_settings()
    if not isinstance(raw, dict):
        return settings
    if raw.get("provider") in PROVIDERS:
        settings["provider"] = raw["provider"]
    if isinstance(raw.get("visitors_can_ask"), bool):
        settings["visitors_can_ask"] = raw["visitors_can_ask"]
    stored = raw.get("providers")
    if isinstance(stored, dict):
        for pid in PROVIDER_IDS:
            entry = stored.get(pid)
            if not isinstance(entry, dict):
                continue
            for field in ("api_key", "model", "base_url", "workspace_id"):
                if isinstance(entry.get(field), str):
                    settings["providers"][pid][field] = entry[field].strip()
    return settings


def load_settings(path: Optional[Path] = None) -> dict:
    """Read saved settings; a missing or corrupt file just means defaults."""
    if path is None:
        migrate_legacy_settings()
    try:
        raw = json.loads(Path(path or SETTINGS_PATH).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        raw = None
    return _normalise(raw)


def save_settings(settings: dict, path: Optional[Path] = None) -> None:
    target = Path(path or SETTINGS_PATH)
    target.parent.mkdir(parents=True, exist_ok=True)
    data = json.dumps(_normalise(settings), indent=2)
    # Write a private temp file and swap it in, so a crash mid-write cannot
    # leave a half-written file and the key is never readable by other users.
    fd, tmp_name = tempfile.mkstemp(dir=str(target.parent), prefix=".settings-", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(data)
        try:
            os.chmod(tmp_name, 0o600)
        except OSError:
            pass  # Windows has no POSIX permissions
        os.replace(tmp_name, target)
    except BaseException:
        try:
            os.unlink(tmp_name)
        except OSError:
            pass
        raise


def _provider_id(provider_id: object) -> str:
    if not isinstance(provider_id, str) or provider_id not in PROVIDERS:
        raise AIError("Unknown AI provider. Choose Claude, OpenAI or Ollama.")
    return provider_id


def _api_key(settings: dict, provider_id: str) -> str:
    saved = settings["providers"][provider_id]["api_key"]
    env_name = PROVIDERS[provider_id]["env_key"]
    return saved or (os.environ.get(env_name, "").strip() if env_name else "")


def _model(settings: dict, provider_id: str) -> str:
    return settings["providers"][provider_id]["model"] or PROVIDERS[provider_id]["default_model"]


def _base_url(settings: dict, provider_id: str) -> str:
    return (settings["providers"][provider_id]["base_url"]
            or PROVIDERS[provider_id]["default_base_url"])


def public_settings(settings: dict) -> dict:
    """What the web page is allowed to see. Never includes a key."""
    settings = _normalise(settings)
    return {
        "provider": settings["provider"],
        "visitors_can_ask": settings["visitors_can_ask"],
        "providers": [
            {
                "id": pid,
                "name": info["name"],
                "needs_key": info["needs_key"],
                "has_key": bool(_api_key(settings, pid)) if info["needs_key"] else False,
                "model": _model(settings, pid),
                "default_model": info["default_model"],
                "base_url": _base_url(settings, pid),
                "sends_data_off_device": info["sends_data_off_device"],
                # Only Anthropic has workspaces; the id is not a secret.
                "uses_workspace": pid == "claude",
                "workspace_id": settings["providers"][pid]["workspace_id"],
            }
            for pid, info in PROVIDERS.items()
        ],
    }


def _clean_base_url(value: str) -> str:
    value = value.strip().rstrip("/")
    parts = urllib.parse.urlsplit(value)
    if parts.scheme not in ("http", "https") or not parts.netloc:
        raise AIError("The server address must start with http:// or https://")
    if parts.query or parts.fragment or parts.username or parts.password:
        raise AIError("The server address should be just the address, like https://example.com")
    # People often paste the address with /v1 on the end; we add that ourselves.
    if value.endswith("/v1"):
        value = value[:-3].rstrip("/")
    return value


def update_settings(settings: dict, changes: dict) -> dict:
    """Return a new settings dict with `changes` applied for one provider."""
    if not isinstance(changes, dict):
        raise AIError("Invalid settings.")
    provider_id = _provider_id(changes.get("provider"))
    updated = _normalise(copy.deepcopy(settings))
    entry = updated["providers"][provider_id]

    if "base_url" in changes:
        base_url = changes["base_url"]
        if not isinstance(base_url, str):
            raise AIError("The server address must be text.")
        previous = entry["base_url"]
        # Empty means "back to the normal address".
        entry["base_url"] = _clean_base_url(base_url) if base_url.strip() else ""
        if entry["base_url"] == PROVIDERS[provider_id]["default_base_url"]:
            entry["base_url"] = ""
        if entry["base_url"] != previous:
            # A saved key belongs to the address it was entered for; never
            # hand it to a different server. It must be typed in again.
            entry["api_key"] = ""

    if "model" in changes:
        model = changes["model"]
        if not isinstance(model, str) or len(model.strip()) > MAX_MODEL_CHARS:
            raise AIError("That model name is not valid.")
        entry["model"] = model.strip()

    if "workspace_id" in changes:
        workspace_id = changes["workspace_id"]
        if not isinstance(workspace_id, str) or not re.fullmatch(
                r"[A-Za-z0-9_-]{0,100}", workspace_id.strip()):
            raise AIError(
                "That workspace ID doesn't look right. It starts with wrkspc_ and "
                "has no spaces.")
        entry["workspace_id"] = workspace_id.strip()

    if changes.get("clear_key"):
        entry["api_key"] = ""
    api_key = changes.get("api_key")
    if api_key is not None and not isinstance(api_key, str):
        raise AIError("The API key must be text.")
    if api_key and api_key.strip():
        key = api_key.strip()
        if len(key) > 500 or any(ch.isspace() or ord(ch) < 33 or ord(ch) > 126 for ch in key):
            raise AIError("That API key doesn't look right. Copy it again without any spaces.")
        entry["api_key"] = key

    if "visitors_can_ask" in changes:
        if not isinstance(changes["visitors_can_ask"], bool):
            raise AIError("Invalid setting for shared visitors.")
        updated["visitors_can_ask"] = changes["visitors_can_ask"]

    updated["provider"] = provider_id
    return updated


# ---------------------------------------------------------------------------
# HTTP
# ---------------------------------------------------------------------------

def _scrub(text: str, secret: str) -> str:
    return text.replace(secret, "[hidden]") if secret else text


def _error_detail(body: bytes) -> str:
    """Pull the human-readable message out of a provider's error reply."""
    try:
        data = json.loads(body.decode("utf-8", "replace"))
    except ValueError:
        return ""
    error = data.get("error") if isinstance(data, dict) else None
    if isinstance(error, dict):
        error = error.get("message")
    return " ".join(error.split())[:300] if isinstance(error, str) else ""


def _http_error(provider_id: str, status: int, detail: str, model: str) -> AIError:
    name = PROVIDERS[provider_id]["name"]
    if "workspace" in detail.lower() and provider_id == "claude":
        return AIError(
            "This Claude API key is not tied to a workspace, so Anthropic needs to "
            "know which workspace to use. Open AI settings, then Advanced, and paste "
            "your Workspace ID (it starts with wrkspc_; find it in the Anthropic "
            "Console under Settings, Workspaces). Or create a new API key inside a "
            "workspace and use that instead.")
    if status in (401, 403):
        if provider_id == "ollama":
            return AIError("Ollama refused the request. Check the server address in the AI settings.")
        return AIError(
            f"The API key was rejected by {name}. Check that it was copied correctly "
            "and is still active, then save it again in the AI settings.")
    if status == 402:
        return AIError(f"{name} reports a billing problem. Check the payment details on your account.")
    if status == 429:
        return AIError(
            f"{name} is not accepting more requests right now: you have hit a rate limit "
            "or run out of credit. Wait a minute and try again, or check your account's "
            "usage and billing.")
    if status == 404:
        if provider_id == "ollama" and model:
            return AIError(
                f"Ollama doesn't have the model \"{model}\" yet. Download it by running "
                f"\"ollama pull {model}\" in a terminal, or pick another model.")
        if model:
            return AIError(
                f"{name} could not find the model \"{model}\". Pick another model in the "
                "AI settings, or check the server address.")
        return AIError(f"{name} was not found at that server address. Check the AI settings.")
    if status == 413:
        return AIError(f"This transcript is too long for {name} to read in one go.")
    if status >= 500:
        return AIError(f"{name} is having trouble right now (error {status}). Try again in a moment.")
    suffix = f": {detail}" if detail else "."
    return AIError(f"{name} did not accept the request (error {status}){suffix}")


def _request(
    provider_id: str,
    method: str,
    url: str,
    headers: Optional[dict] = None,
    body: Optional[dict] = None,
    timeout: float = ASK_TIMEOUT,
    secret: str = "",
    model: str = "",
) -> dict:
    """Send one JSON request and return the JSON reply, or raise AIError."""
    name = PROVIDERS[provider_id]["name"]
    data = None if body is None else json.dumps(body).encode("utf-8")
    request = urllib.request.Request(url, data=data, method=method)
    request.add_header("Accept", "application/json")
    # Some services turn away Python's default "Python-urllib" user agent.
    request.add_header("User-Agent", "SkimWatch")
    if data is not None:
        request.add_header("Content-Type", "application/json")
    for key, value in (headers or {}).items():
        request.add_header(key, value)
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            payload = response.read()
    except urllib.error.HTTPError as exc:
        detail = _scrub(_error_detail(exc.read()), secret)
        raise _http_error(provider_id, exc.code, detail, model) from None
    except (socket.timeout, TimeoutError):
        raise AIError(f"{name} took too long to answer. Try again, or ask a shorter question.") from None
    except urllib.error.URLError as exc:
        reason = exc.reason
        if isinstance(reason, (socket.timeout, TimeoutError)):
            raise AIError(
                f"{name} took too long to answer. Try again, or ask a shorter question.") from None
        if provider_id == "ollama":
            raise AIError(
                "Ollama doesn't seem to be running. Start the Ollama app (or run "
                "\"ollama serve\" in a terminal) and try again.") from None
        if isinstance(reason, ConnectionRefusedError):
            raise AIError(f"Could not connect to {name}. Check the server address in the AI settings.") from None
        raise AIError(f"Could not reach {name}. Check your internet connection and try again.") from None
    except (OSError, ValueError):
        # ValueError covers things like an address urllib cannot parse.
        raise AIError(f"Could not reach {name}. Check the server address and your connection.") from None
    try:
        parsed = json.loads(payload.decode("utf-8"))
    except ValueError:
        parsed = None
    if not isinstance(parsed, dict):
        raise AIError(f"{name} sent back something unexpected. Check the server address in the AI settings.")
    return parsed


def _auth_headers(settings: dict, provider_id: str) -> dict:
    key = _api_key(settings, provider_id)
    if PROVIDERS[provider_id]["needs_key"] and not key:
        raise AIError(f"Add your {PROVIDERS[provider_id]['name']} API key in the AI settings first.")
    if provider_id == "claude":
        headers = {"x-api-key": key, "anthropic-version": ANTHROPIC_VERSION}
        # Keys that are not tied to one workspace must say which to use.
        workspace_id = settings["providers"]["claude"].get("workspace_id")
        if workspace_id:
            headers["anthropic-workspace-id"] = workspace_id
        return headers
    if provider_id == "openai":
        return {"Authorization": f"Bearer {key}"}
    return {}


# ---------------------------------------------------------------------------
# Models
# ---------------------------------------------------------------------------

def list_models(settings: dict, provider_id: str) -> list[str]:
    """Ask the provider which models are available."""
    provider_id = _provider_id(provider_id)
    settings = _normalise(settings)
    base = _base_url(settings, provider_id)
    headers = _auth_headers(settings, provider_id)
    key = _api_key(settings, provider_id)

    if provider_id == "ollama":
        reply = _request("ollama", "GET", base + "/api/tags", timeout=MODELS_TIMEOUT)
        names = [m.get("name") for m in reply.get("models") or [] if isinstance(m, dict)]
        return sorted({n for n in names if isinstance(n, str) and n}, key=str.lower)

    if provider_id == "claude":
        # The API lists newest first, which is the most useful order to show.
        models: list[str] = []
        after = ""
        for _ in range(10):  # paged; far more pages than Anthropic has models
            url = base + "/v1/models?limit=1000" + (
                "&after_id=" + urllib.parse.quote(after) if after else "")
            reply = _request("claude", "GET", url, headers, timeout=MODELS_TIMEOUT, secret=key)
            page = [m.get("id") for m in reply.get("data") or [] if isinstance(m, dict)]
            models += [m for m in page if isinstance(m, str) and m and m not in models]
            after = reply.get("last_id") or ""
            if not reply.get("has_more") or not after:
                break
        return models

    reply = _request("openai", "GET", base + "/v1/models", headers,
                     timeout=MODELS_TIMEOUT, secret=key)
    ids = {m.get("id") for m in reply.get("data") or [] if isinstance(m, dict)}
    ids = {i for i in ids if isinstance(i, str) and i}
    chat = {i for i in ids if not any(word in i.lower() for word in _NOT_CHAT)}
    return sorted(chat or ids, key=str.lower)


# ---------------------------------------------------------------------------
# Asking
# ---------------------------------------------------------------------------

def _validate_messages(messages: object) -> list[dict]:
    if not isinstance(messages, list) or not messages:
        raise AIError("Type a question first.")
    if len(messages) > MAX_MESSAGES:
        raise AIError("This conversation has become too long. Start a new one to keep asking.")
    clean: list[dict] = []
    for message in messages:
        if not isinstance(message, dict):
            raise AIError("Invalid conversation.")
        role, content = message.get("role"), message.get("content")
        if role not in ("user", "assistant") or not isinstance(content, str):
            raise AIError("Invalid conversation.")
        content = content.strip()
        if not content:
            raise AIError("Type a question first." if role == "user" else "Invalid conversation.")
        if len(content) > MAX_MESSAGE_CHARS:
            raise AIError("That message is too long. Please shorten it.")
        clean.append({"role": role, "content": content})
    if clean[-1]["role"] != "user":
        raise AIError("The last message must be your question.")
    return clean


def _transcript_block(transcript: str, title: str) -> str:
    heading = f"Video title: {title.strip()}\n\n" if title and title.strip() else ""
    return f"{heading}<transcript>\n{transcript.strip()}\n</transcript>"


def _ask_claude(settings: dict, model: str, document: str, messages: list[dict]) -> tuple[str, str]:
    headers = _auth_headers(settings, "claude")
    body: dict = {
        "model": model,
        "max_tokens": CLAUDE_MAX_TOKENS,
        "system": [
            {"type": "text", "text": INSTRUCTIONS},
            # Everything up to here is identical for each follow-up question,
            # so Anthropic can reuse it at a fraction of the price.
            {"type": "text", "text": document, "cache_control": {"type": "ephemeral"}},
        ],
        "messages": messages,
    }
    if model in CLAUDE_FALLBACK_MODELS:
        body["fallbacks"] = "default"
        headers["anthropic-beta"] = CLAUDE_FALLBACK_BETA
    reply = _request("claude", "POST", _base_url(settings, "claude") + "/v1/messages",
                     headers, body, secret=headers["x-api-key"], model=model)
    # A refusal arrives as a normal reply, so check before reading the text.
    if reply.get("stop_reason") == "refusal":
        raise AIError("Claude declined to answer that question. Try rephrasing it, or pick another model.")
    blocks = reply.get("content") if isinstance(reply.get("content"), list) else []
    answer = "".join(
        b["text"] for b in blocks
        if isinstance(b, dict) and b.get("type") == "text" and isinstance(b.get("text"), str))
    served_by = reply.get("model") if isinstance(reply.get("model"), str) else ""
    return answer, served_by or model


def _ask_openai(settings: dict, model: str, document: str, messages: list[dict]) -> tuple[str, str]:
    headers = _auth_headers(settings, "openai")
    body = {
        "model": model,
        "messages": [{"role": "system", "content": INSTRUCTIONS + "\n\n" + document}] + messages,
    }
    key = _api_key(settings, "openai")
    reply = _request("openai", "POST", _base_url(settings, "openai") + "/v1/chat/completions",
                     headers, body, secret=key, model=model)
    try:
        answer = reply["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError):
        answer = ""
    return (answer if isinstance(answer, str) else ""), model


def _ask_ollama(settings: dict, model: str, document: str, messages: list[dict]) -> tuple[str, str]:
    system = INSTRUCTIONS + "\n\n" + document
    # Rough size in tokens (about 3 characters each) plus room for the answer.
    needed = (len(system) + sum(len(m["content"]) for m in messages)) // 3 + 2048
    body = {
        "model": model,
        "stream": False,
        "messages": [{"role": "system", "content": system}] + messages,
        "options": {"num_ctx": max(OLLAMA_MIN_CONTEXT, min(needed, OLLAMA_MAX_CONTEXT))},
    }
    reply = _request("ollama", "POST", _base_url(settings, "ollama") + "/api/chat",
                     body=body, model=model)
    message = reply.get("message")
    answer = message.get("content") if isinstance(message, dict) else ""
    return (answer if isinstance(answer, str) else ""), model


def ask(settings: dict, transcript: str, title: str, messages: list[dict]) -> dict:
    """Answer the latest question in `messages` using the transcript."""
    settings = _normalise(settings)
    provider_id = settings["provider"]
    if provider_id not in PROVIDERS:
        raise AIError("Choose an AI provider in the AI settings first.")
    if not isinstance(transcript, str) or not transcript.strip():
        raise AIError("This transcript is empty, so there is nothing to ask about.")
    clean = _validate_messages(messages)
    model = _model(settings, provider_id)
    document = _transcript_block(transcript, title if isinstance(title, str) else "")

    handler = {"claude": _ask_claude, "openai": _ask_openai, "ollama": _ask_ollama}[provider_id]
    answer, served_by = handler(settings, model, document, clean)
    answer = answer.strip()
    if not answer:
        raise AIError(f"{PROVIDERS[provider_id]['name']} sent back an empty answer. Try asking again.")
    return {"answer": answer, "provider": provider_id, "model": served_by}
