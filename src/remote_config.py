"""Remote-only configuration validation. No network or model imports here."""
from copy import deepcopy
import ipaddress
import os
import re
from urllib.parse import urlsplit


def contains_secret(obj):
    if isinstance(obj, dict):
        return any(str(k).lower() in {"api_key", "access_token", "token", "secret", "password", "authorization"}
                   or contains_secret(v) for k, v in obj.items())
    if isinstance(obj, list):
        return any(contains_secret(v) for v in obj)
    return False


def check_url(value):
    if not isinstance(value, str) or "REPLACE_WITH" in value.upper():
        raise ValueError("Fill in the remote API base address")
    parsed = urlsplit(value)
    host = (parsed.hostname or "").lower().rstrip(".")
    if parsed.scheme != "https" or not host or parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise ValueError("Remote API base must be an HTTPS URL without embedded credentials")
    if host in {"localhost", "local", "lan"} or host.endswith((".localhost", ".local", ".lan")):
        raise ValueError("Local model endpoints are disabled")
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        address = None
    if address:
        address = address.ipv4_mapped if isinstance(address, ipaddress.IPv6Address) and address.ipv4_mapped else address
        # Explicit addresses must be publicly routable; private LANs can host
        # local model servers just as loopback can. DNS aliases are not resolved.
        if not address.is_global:
            raise ValueError("Local model endpoints are disabled")


def check_remote_targets(raw):
    for role in ("llm", "summary_llm", "embedding"):
        value = raw.get(role)
        if not isinstance(value, str) or not value.strip():
            raise ValueError("Explicit llm, summary_llm and embedding settings required")
        if "REPLACE_WITH" in value.upper():
            raise ValueError("Fill in remote model names")
    for key in ("llm_config", "summary_llm_config"):
        models = (raw.get(key) or {}).get("model_list", [])
        if not models or not any(m.get("model_name") == raw[key.replace("_config", "")] for m in models):
            raise ValueError("Remote model_list must match the configured model alias")
        for model in models:
            params = model.get("litellm_params", {})
            name = params.get("model", "")
            if not name.startswith("openai/") or "REPLACE_WITH" in name.upper():
                raise ValueError("This remote template requires an explicit OpenAI-compatible model")
            check_url(params.get("api_base"))
    if not raw["embedding"].startswith("openai/"):
        raise ValueError("A remote OpenAI-compatible embedding model is required; local embeddings are disabled")
    check_url((raw.get("embedding_config") or {}).get("kwargs", {}).get("api_base"))
    # Reject URL-bearing alternate routes rather than leave an unvalidated fallback.
    def scan(obj):
        if isinstance(obj, dict):
            for key, value in obj.items():
                if str(key).lower() in {"api_base", "base_url"}:
                    check_url(value)
                scan(value)
        elif isinstance(obj, list):
            for value in obj:
                scan(value)
    scan(raw)


def read_credential(name):
    if not isinstance(name, str) or not re.fullmatch(r"PAPERQA_[A-Z0-9_]+_API_KEY", name):
        raise ValueError("Use a dedicated PAPERQA_*_API_KEY environment variable")
    value = os.environ.get(name, "")
    if not value and os.name == "nt":
        # Read only the explicitly named user variable, never enumerate credentials.
        import winreg
        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, "Environment") as key:
                value, _ = winreg.QueryValueEx(key, name)
        except FileNotFoundError:
            pass
    return value if isinstance(value, str) else ""


def is_remote_configured(raw):
    """A distributable template is not a usable provider or model choice."""
    params = raw["llm_config"]["model_list"][0]["litellm_params"]
    embedding_base = raw["embedding_config"]["kwargs"]["api_base"]
    return (not any((urlsplit(address).hostname or "").endswith(".invalid")
                    for address in (params["api_base"], embedding_base))
            and params["model"] != "openai/your-chat-model"
            and raw["embedding"] != "openai/your-embedding-model")


def prepare_remote_settings(raw, require_credentials=True):
    if contains_secret(raw):
        raise ValueError("Keep API keys/tokens in environment, not settings files")
    check_remote_targets(raw)
    if require_credentials and not is_remote_configured(raw):
        raise ValueError("Configure your own remote API and model names before asking")
    names = raw.get("credential_envs", {})
    if set(names) != {"chat", "embedding"}:
        raise ValueError("Specify chat and embedding credential environment variable names")
    credentials = {role: read_credential(name) for role, name in names.items()}
    availability = {role: bool(value.strip()) for role, value in credentials.items()}
    if require_credentials and not all(availability.values()):
        missing = ", ".join(names[role] for role, present in availability.items() if not present)
        raise ValueError(f"Missing remote API credentials: {missing}")
    resolved = deepcopy(raw)
    resolved.pop("credential_envs", None)
    for key in ("llm_config", "summary_llm_config"):
        for model in resolved[key]["model_list"]:
            model["litellm_params"]["api_key"] = credentials["chat"]
    resolved["embedding_config"]["kwargs"]["api_key"] = credentials["embedding"]
    return resolved, availability
