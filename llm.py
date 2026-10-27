"""The project's one model: Google Gemini.

Setup:
    pip install langchain-google-genai python-dotenv

Then either set the key in the shell:
    set GOOGLE_API_KEY=your_key          (Windows cmd)
    $env:GOOGLE_API_KEY="your_key"       (PowerShell)

or put it in a file called .env in the project root:
    GOOGLE_API_KEY=your_key
    GEMINI_MODEL=gemini-2.0-flash

`get_llm()` returns None when no key is set, and every agent falls back to
a pre-written sentence. That is deliberate: the pipeline must still run if
the key is missing, the quota is spent, or the network drops mid-demo.

The model writes language. It never computes a number that appears in the
results -- those come from DuckDB.
"""
import os

# Optional: load a .env file if python-dotenv is installed, so the key does
# not have to be re-set in every new terminal.
from dotenv import load_dotenv

load_dotenv()


DEFAULT_MODEL = "gemini-3.5-flash"


def model_name() -> str:
    """Which Gemini model to use. Override with GEMINI_MODEL."""
    return os.getenv("GEMINI_MODEL", DEFAULT_MODEL)


def get_llm():
    """Return a Gemini chat model, or None if no API key is set."""
    if not os.getenv("GOOGLE_API_KEY"):
        return None

    from langchain_google_genai import ChatGoogleGenerativeAI
    return ChatGoogleGenerativeAI(
        model=model_name(),
        temperature=0,          # deterministic: same input, same wording
    )


def available() -> bool:
    return get_llm() is not None


def provider_name() -> str:
    """What actually ran. Printed and saved, so a demo cannot misreport itself."""
    if os.getenv("GOOGLE_API_KEY"):
        return f"gemini:{model_name()}"
    return "none (deterministic fallback)"


def response_text(content: object) -> str:
    """Extract text from LangChain message content."""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        text_parts = []
        for part in content:
            if isinstance(part, str):
                text_parts.append(part)
            elif isinstance(part, dict) and part.get("type") == "text":
                text = part.get("text")
                if isinstance(text, str):
                    text_parts.append(text)
        if text_parts:
            return "".join(text_parts)
        raise TypeError("Gemini response content contains no text")
    raise TypeError(
        f"Unsupported Gemini response content type: {type(content).__name__}"
    )


def complete(prompt: str, fallback: str) -> str:
    """Run a prompt, or return `fallback` if Gemini is unavailable."""
    llm = get_llm()
    if llm is None:
        return fallback
    try:
        return response_text(llm.invoke(prompt).content).strip()
    except Exception as exc:                      # noqa: BLE001
        return f"{fallback}\n\n[Gemini call failed: {exc}]"


def check() -> None:
    """Run `python llm.py` to confirm the key and model work."""
    print(f"provider : {provider_name()}")
    if not os.getenv("GOOGLE_API_KEY"):
        print("GOOGLE_API_KEY is not set -- agents will use their fallbacks.")
        return
    try:
        reply = response_text(
            get_llm().invoke("Reply with the single word: ready").content
        )
        print(f"test call: {reply.strip()}")
    except Exception as exc:                      # noqa: BLE001
        print(f"test call FAILED: {exc}")
        print("Check the key, the model name, and that the API is enabled.")


if __name__ == "__main__":
    check()