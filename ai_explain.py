"""Optional Gemini explanation. The app works fully without it.

The model only rephrases the rule-based results in the farmer's language.
It is told not to add facts, and the UI labels its output as AI-written.
"""
import os

LANG_NAMES = {"en": "English", "ta": "Tamil", "te": "Telugu", "hi": "Hindi"}
DEFAULT_MODEL = "gemini-2.5-flash"
_chosen_model = None  # a working model found automatically, remembered for this app run


def get_api_key() -> str | None:
    key = os.environ.get("GEMINI_API_KEY")
    if key:
        return key.strip()
    try:  # Streamlit secrets (Community Cloud)
        import streamlit as st
        key = st.secrets.get("GEMINI_API_KEY")
        return key.strip() if key else None
    except Exception:
        return None


def is_configured() -> bool:
    return bool(get_api_key())


def _configured_model() -> str:
    name = os.environ.get("GEMINI_MODEL")
    if not name:
        try:
            import streamlit as st
            name = st.secrets.get("GEMINI_MODEL")
        except Exception:
            name = None
    return (name or DEFAULT_MODEL).strip()


def safe_error(e: Exception) -> str:
    """Error text that is safe to show: never contains the API key."""
    text = f"{type(e).__name__}: {e}"
    key = get_api_key()
    if key:
        text = text.replace(key, "[key hidden]")
    return text[:500]


def _model_problem(e: Exception) -> bool:
    """True when the error looks like 'this model name is not available' (retired or misspelled)."""
    msg = str(e).lower()
    return any(s in msg for s in ("not_found", "404", "not found", "not supported", "no longer available",
                                  "is not available", "deprecated"))


def _discover(client) -> list[str]:
    """Ask Google which models this key can use, and list suitable text 'flash' models, best first."""
    names = []
    for m in client.models.list():
        name = (getattr(m, "name", "") or "").replace("models/", "")
        actions = getattr(m, "supported_actions", None) or []
        if ("generateContent" in actions and "flash" in name
                and not any(x in name for x in ("image", "tts", "live", "audio", "embed", "thinking", "robotics"))):
            names.append(name)
    names.sort(reverse=True)  # newer version numbers first
    names.sort(key=lambda n: ("preview" in n or "exp" in n, "lite" in n))  # stable models first, lite last
    return names


def _generate(client, model: str, prompt: str) -> str:
    resp = client.models.generate_content(model=model, contents=prompt)
    text = (resp.text or "").strip()
    if not text:
        raise RuntimeError("The model returned no text (the answer may have been blocked).")
    return text


def explain(summary: str, lang: str) -> str:
    """summary is plain text built from the rule results. Raises on API errors."""
    global _chosen_model
    from google import genai

    client = genai.Client(api_key=get_api_key())
    prompt = (
        f"You help small farmers in India. Explain the following farm-residue suggestions "
        f"in simple {LANG_NAMES.get(lang, 'English')}, in at most 120 words, using short sentences. "
        f"Use ONLY the information given below. Do not add new uses, numbers, prices or safety "
        f"claims. Keep every caution. End by telling the farmer to confirm with a local "
        f"agricultural officer.\n\n{summary}"
    )
    first_model = _chosen_model or _configured_model()
    try:
        return _generate(client, first_model, prompt)
    except Exception as first_error:
        if not _model_problem(first_error):
            raise
        for name in _discover(client):  # the configured model is gone: try what Google offers now
            if name == first_model:
                continue
            try:
                text = _generate(client, name, prompt)
            except Exception:
                continue
            _chosen_model = name
            return text
        raise first_error
