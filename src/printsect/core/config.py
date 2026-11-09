"""Project root discovery and configuration.

`printsect.yaml` is shared with the project; `printsect.local.yaml` sits on top of it
for settings that belong to one person (tools on their machine) and is not committed.
"""

from __future__ import annotations

import copy
import difflib
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Union

from .errors import ConfigError
from .fsutil import require_plain

CONFIG_NAME = "printsect.yaml"
LOCAL_CONFIG_NAME = "printsect.local.yaml"
STATE_DIR = ".printsect"

# What marks a project root, nearest first. The config and state come first so that a
# sub-project inside a larger checkout can have its own.
ROOT_MARKERS = (CONFIG_NAME, STATE_DIR, ".git", ".svn")

DEFAULTS: dict[str, Any] = {
    "version": 1,
    "marker": {"prefix": "PSECT"},
    "probe": {
        # {tag} -> "PSECT-AI 7 group", {n} -> 7, {where} -> "file.c:61"
        "template": 'printf("[{tag}] %s:%d\\n", __FILE__, __LINE__);',
        "encodings": ["utf-8", "cp932"],
        "extensions": [".c", ".h", ".cc", ".cpp", ".cxx", ".hpp", ".hh", ".inc"],
    },
    "limits": {"active_probes": 20},
    # What is built from what. The builtin reads what `make -n` would run.
    "deps": {
        "type": "make", "command": "make -n -B -w", "cwd": ".", "timeout": 300,
        "headers": True,
    },
}

PREFIX_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_]*$")
DEVICE_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}$")

# A schema maps each setting to its type (or a tuple of types), or to the schema of a
# nested mapping. Tools declare theirs; a plugin (a deployer, a deps reader) declares the
# keys of its own section as SETTINGS.
Schema = dict[str, Union[type, tuple[type, ...], "Schema"]]

# Settings of printsect itself, used by more than one tool.
CORE_SCHEMA: Schema = {"version": int, "marker": {"prefix": str}, "devices": dict,
                       "lang": str}

# The languages the guides and the settings templates come in.
LANGS = ("en", "ja")


def user_folder() -> Path:
    """The user's own printsect folder (never inside a project)."""
    if os.environ.get("PSECT_CONFIG_DIR"):
        return Path(os.environ["PSECT_CONFIG_DIR"])
    if os.name == "nt" and os.environ.get("APPDATA"):
        return Path(os.environ["APPDATA"]) / "printsect"
    base = os.environ.get("XDG_CONFIG_HOME") or str(Path.home() / ".config")
    return Path(base) / "printsect"


def language(asked: str | None = None, settings: dict[str, Any] | None = None) -> str:
    """The language to use: asked (--lang), else PSECT_LANG, else lang: in the settings,
    else English."""
    lang = asked or os.environ.get("PSECT_LANG") or (settings or {}).get("lang") or "en"
    lang = str(lang).lower().split("_")[0].split("-")[0]  # ja_JP.UTF-8 -> ja
    if lang not in LANGS:
        raise ConfigError(f"lang: one of {', '.join(LANGS)} (got {lang!r})")
    return lang

# The device chosen with `psect use`, kept per project (not shared: it is this PC's).
DEVICE_FILE = "device"


def _type_names(want: type | tuple[type, ...]) -> str:
    kinds = want if isinstance(want, tuple) else (want,)
    return " or ".join({"str": "text", "int": "a number", "float": "a number",
                        "bool": "true/false", "list": "a list", "dict": "a mapping"}
                       .get(k.__name__, k.__name__) for k in kinds)


def problems(data: Any, schema: Schema, where: str = "") -> list[str]:
    """What is wrong in `data` against `schema`: unknown keys (with a near match) and
    values of the wrong type. Empty when all is well."""
    if not isinstance(data, dict):
        return [f"{where or 'settings'}: expected a mapping, got {type(data).__name__}"]
    found = []
    for key, value in data.items():
        path = f"{where}.{key}" if where else str(key)
        want = schema.get(str(key))
        if want is None:
            near = difflib.get_close_matches(str(key), list(schema), n=1)
            found.append(f"{path}: not a setting" + (f" (did you mean {near[0]}?)" if near else ""))
        elif isinstance(want, dict):
            found += problems(value, want, path)
        elif value is not None:
            kinds = want if isinstance(want, tuple) else (want,)
            if not isinstance(value, kinds) or (isinstance(value, bool) and bool not in kinds):
                found.append(f"{path}: expected {_type_names(want)}, got {value!r}")
    return found


def check(data: Any, schema: Schema, where: str = "") -> None:
    """Raise a ConfigError naming every problem, so all of them can be fixed at once."""
    found = problems(data, schema, where)
    if found:
        raise ConfigError(
            f"{CONFIG_NAME} / {LOCAL_CONFIG_NAME}:\n" + "\n".join(f"  {p}" for p in found))


def _merge(base: dict[str, Any], over: dict[str, Any]) -> dict[str, Any]:
    out = copy.deepcopy(base)
    for key, value in over.items():
        if isinstance(value, dict) and isinstance(out.get(key), dict):
            out[key] = _merge(out[key], value)
        else:
            out[key] = value
    return out


def find_root(start: Path) -> Path:
    """The nearest folder at or above `start` holding a root marker, else `start`."""
    start = start.resolve()
    for folder in (start, *start.parents):
        if any(os.path.lexists(folder / marker) for marker in ROOT_MARKERS):
            return folder
    return start


def _load_yaml(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    import yaml  # loaded only when a config exists, to keep start-up light

    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except yaml.YAMLError as exc:
        raise ConfigError(f"{path.name}: {exc}") from exc
    if not isinstance(data, dict):
        raise ConfigError(f"{path.name}: the top level must be a mapping")
    return data


@dataclass
class Project:
    root: Path
    settings: dict[str, Any]  # with the chosen device's settings laid over
    device: str | None = None  # the device in use, when devices are set up
    devices: tuple[str, ...] = ()  # every device set up

    def require_device(self) -> None:
        """Stop when several devices are set up and none is chosen: a build or a deploy
        for the wrong one is worse than asking."""
        if self.devices and self.device is None:
            raise ConfigError(f"several devices are set up ({', '.join(self.devices)}) and "
                              "none is chosen: psect use NAME (or --device NAME)")

    @property
    def state_dir(self) -> Path:
        return require_plain(self.root / STATE_DIR)

    def state_file(self, name: str) -> Path:
        """A file or folder in the state folder; stops if either is a link."""
        return require_plain(self.state_dir / name)

    @property
    def prefix(self) -> str:
        return str(self.settings["marker"]["prefix"])

    @property
    def template(self) -> str:
        return str(self.settings["probe"]["template"])

    @property
    def encodings(self) -> list[str]:
        return list(self.settings["probe"]["encodings"])

    @property
    def extensions(self) -> tuple[str, ...]:
        return tuple(e.lower() for e in self.settings["probe"]["extensions"])

    def rel(self, path: Path) -> str:
        """`path` relative to the root, with forward slashes, as the ledger keeps it."""
        try:
            return path.resolve().relative_to(self.root).as_posix()
        except ValueError as exc:
            raise ConfigError(f"{path} is outside the project {self.root}") from exc

    def abs(self, rel: str) -> Path:
        """A project path made absolute; one that leads outside the root is refused.

        Checked twice: as written (`../`, an absolute path), and after following
        symbolic links, so a link inside the project cannot lead a write outside it.
        """
        path = Path(os.path.normpath(self.root / rel))
        if not path.is_relative_to(self.root):
            raise ConfigError(f"{rel} is outside the project {self.root}")
        real = path.resolve()
        if not real.is_relative_to(self.root.resolve()):
            raise ConfigError(f"{rel} leads outside the project {self.root} (to {real})")
        return path


def chosen_device(root: Path) -> str | None:
    """The device `psect use` chose for this project, if any."""
    path = require_plain(root / STATE_DIR, root / STATE_DIR / DEVICE_FILE)
    try:
        name = path.read_text(encoding="utf-8").strip()
    except OSError:
        return None
    return name or None


def load_project(root: Path | None = None, cwd: Path | None = None,
                 device: str | None = None) -> Project:
    """The project and its settings: printsect.yaml, then printsect.local.yaml, then the
    device's part of `devices:` laid over both.

    The device is the one named here (--device), else PSECT_DEVICE, else the one chosen
    with `psect use`, else the only one there is.
    """
    root = (root or find_root(cwd or Path.cwd())).resolve()
    settings = _merge(DEFAULTS, _load_yaml(root / CONFIG_NAME))
    settings = _merge(settings, _load_yaml(root / LOCAL_CONFIG_NAME))
    devices = settings.get("devices") or {}
    if not isinstance(devices, dict) or not all(isinstance(v, dict) for v in devices.values()):
        raise ConfigError("devices: a mapping of device name -> the settings that differ")
    names = tuple(str(k) for k in devices)
    for n in names:
        if not DEVICE_RE.match(n):
            raise ConfigError(f"devices: {n!r}: a name of letters, digits and _ . -")
    asked = device or os.environ.get("PSECT_DEVICE") or None
    name = asked or chosen_device(root)
    if name is not None and name not in devices:
        if asked or names:  # a stale choice with no devices left is simply dropped
            known = ", ".join(names) or "none are set up"
            raise ConfigError(f"no device {name!r} in devices: ({known})")
        name = None
    if name is None and len(names) == 1:
        name = names[0]
    if name is not None:
        settings = _merge(settings, devices[name])
    prefix = str(settings["marker"]["prefix"])
    if not PREFIX_RE.match(prefix):
        raise ConfigError(f"marker.prefix must be letters, digits and _: {prefix!r}")
    return Project(root=root, settings=settings, device=name, devices=names)


def init_template(name: str, lang: str = "en") -> str:
    """A settings file `psect init` writes (init_config.yaml or init_local.yaml), in
    `lang` when there is one in it."""
    from importlib.resources import files

    if lang != "en":
        local = files(__package__).joinpath(name.replace(".yaml", f".{lang}.yaml"))
        if local.is_file():
            return local.read_text(encoding="utf-8")
    return files(__package__).joinpath(name).read_text(encoding="utf-8")
