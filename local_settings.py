"""Device preferences, kept outside the game, save slots and bundled files."""
from __future__ import annotations

import json
import os
import sys
import tempfile
import base64
import ctypes
from copy import deepcopy
from settings_widgets import NUMERIC_SETTINGS
from agent_prompts import DEFAULT_PROMPTS
from pathlib import Path


def settings_path() -> Path:
    """Resolve the user's configuration directory independently of the executable."""
    if sys.platform == "win32":
        root = Path(os.environ.get("LOCALAPPDATA") or Path.home() / "AppData" / "Local")
    elif sys.platform == "darwin":
        root = Path.home() / "Library" / "Application Support"
    else:
        root = Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config")
    return root / "CheckMate" / "settings.json"


def default_profile():
    return {"endpoint": "", "api_key": "", "model": "", "max_tokens": 32768,
            "token_parameter": "max_tokens", "timeout": 120, "temperature": None,
            "reasoning_effort": "default", "vision": True, "streaming": True}


def _crypt_key(value: bytes, decrypt=False) -> bytes:
    """Windows DPAPI ties saved API credentials to the signed-in local user."""
    if sys.platform != "win32":
        raise OSError("Persistent API credentials require Windows DPAPI")
    from ctypes import wintypes
    class Blob(ctypes.Structure):
        _fields_ = [("size", wintypes.DWORD), ("data", ctypes.POINTER(ctypes.c_ubyte))]
    buffer = ctypes.create_string_buffer(value)
    source = Blob(len(value), ctypes.cast(buffer, ctypes.POINTER(ctypes.c_ubyte)))
    output = Blob()
    function = (ctypes.windll.crypt32.CryptUnprotectData if decrypt else ctypes.windll.crypt32.CryptProtectData)
    function.argtypes = [ctypes.c_void_p] * 5 + [wintypes.DWORD, ctypes.c_void_p]
    function.restype = wintypes.BOOL
    if not function(ctypes.byref(source), None, None, None, None, 1, ctypes.byref(output)):
        raise OSError("Could not access local API credentials")
    try:
        return ctypes.string_at(output.data, output.size)
    finally:
        free = ctypes.windll.kernel32.LocalFree
        free.argtypes = [ctypes.c_void_p]
        free.restype = ctypes.c_void_p
        free(output.data)


def _preferences(data) -> dict:
    if not isinstance(data, dict):
        data = {}
    legacy_side = "black" if data.get("llm_color") == "black" else "white"
    players = data.get("player_types", {})
    if not isinstance(players, dict):
        players = {}
    profiles = data.get("api_profiles", {})
    if not isinstance(profiles, dict):
        profiles = {}
    prompts = data.get("prompts", {})
    if not isinstance(prompts, dict):
        prompts = {}
    result = {
        "agent_play_mode": data.get("agent_play_mode") is True,
        "llm_color": legacy_side,
        "play_control_mode": "auto" if data.get("play_control_mode") == "auto" else "step",
        "player_types": {side: (players.get(side) if players.get(side) in ("human", "llm")
                                else "llm" if side == legacy_side else "human") for side in ("black", "white")},
        "api_profiles": {},
        "prompts": {name: value if isinstance(value := prompts.get(name), str) and value.strip()
                    and len(value) <= 65536 else default for name, default in DEFAULT_PROMPTS.items()},
    }
    for side in ("black", "white"):
        profile = default_profile()
        raw = profiles.get(side, {})
        if not isinstance(raw, dict):
            raw = {}
        for key in ("endpoint", "api_key", "model"):
            if isinstance(raw.get(key), str):
                profile[key] = raw[key][:4096]
        for key in ("max_tokens", "timeout"):
            minimum, maximum, _ = NUMERIC_SETTINGS[key]
            if type(raw.get(key)) is int and minimum <= raw[key] <= maximum:
                profile[key] = raw[key]
        if raw.get("token_parameter") in ("max_tokens", "max_completion_tokens"):
            profile["token_parameter"] = raw["token_parameter"]
        temperature = raw.get("temperature")
        if type(temperature) in {int, float} and 0 <= temperature <= 2:
            profile["temperature"] = temperature
        if raw.get("reasoning_effort") in ("default", "none", "low", "medium", "high"):
            profile["reasoning_effort"] = raw["reasoning_effort"]
        profile["vision"] = raw.get("vision", True) is True
        profile["streaming"] = raw.get("streaming", True) is True
        result["api_profiles"][side] = profile
    return result


class LocalSettings:
    def __init__(self, path: Path | None = None):
        self.path = Path(path) if path is not None else settings_path()
        self.error = None
        self._saved = None

    def load(self) -> dict:
        self.error = None
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            if not isinstance(data, dict):
                raise ValueError("Settings must be an object")
            profiles = data.get("api_profiles")
            for profile in (profiles.values() if isinstance(profiles, dict) else []):
                if isinstance(profile, dict) and profile.get("api_key_protected"):
                    try:
                        profile["api_key"] = _crypt_key(base64.b64decode(profile["api_key_protected"]), decrypt=True).decode("utf-8")
                    except (OSError, ValueError, TypeError):
                        self.error = "Local API credentials could not be restored. Enter the API key again."
        except FileNotFoundError:
            data = {}
        except (OSError, ValueError):
            self.error = "Local settings could not be loaded. Default settings are in use."
            data = {}
        self._saved = _preferences(data)
        return deepcopy(self._saved)

    def save(self, preferences: dict) -> bool:
        """Write only a changed configuration, replacing it after a complete write."""
        data = _preferences(preferences)
        if data == self._saved:
            return False
        self.path.parent.mkdir(parents=True, exist_ok=True)
        persisted = _preferences(data)
        for profile in persisted["api_profiles"].values():
            key = profile.pop("api_key")
            if key:
                profile["api_key_protected"] = base64.b64encode(_crypt_key(key.encode("utf-8"))).decode("ascii")
        temporary = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="w", encoding="utf-8", dir=self.path.parent,
                prefix="settings-", suffix=".tmp", delete=False,
            ) as stream:
                temporary = Path(stream.name)
                json.dump({"version": 2, **persisted}, stream, indent=2)
                stream.write("\n")
            os.replace(temporary, self.path)
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)
        self._saved = data
        self.error = None
        return True
