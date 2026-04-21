import time
from google import genai
from google.genai import types
from openai import OpenAI
import config

client = genai.Client(
    vertexai=True,
    project=config.VERTEX_PROJECT,
    location=config.VERTEX_LOCATION,
)

_CONTINUATION_PROMPT = (
    "Continue the Java code from exactly where you stopped. "
    "Output only the remaining Java code. "
    "No explanations, no markdown fences, no package declaration — "
    "only the code that comes after where you stopped."
)


def _extract_text(response):
    """Safely extracts the text from a GenerateContentResponse."""
    try:
        return response.text
    except Exception:
        return None


def _is_max_tokens(response):
    """Returns True if the first candidate's finish_reason is MAX_TOKENS."""
    candidates = getattr(response, 'candidates', None)
    if not candidates:
        return False
    finish_reason = getattr(candidates[0], 'finish_reason', None)
    # FinishReason is a CaseInSensitiveEnum whose string value is 'MAX_TOKENS';
    # the == comparison works for both the enum and a plain string.
    return finish_reason == types.FinishReason.MAX_TOKENS


def _generate(prompt_contents):
    """Single generate_content call — prompt_contents may be a str or a list."""
    return client.models.generate_content(
        model=config.LLM_MODEL,
        contents=prompt_contents,
        config=types.GenerateContentConfig(
            max_output_tokens=config.LLM_MAX_TOKENS,
            temperature=config.LLM_TEMPERATURE,
        ),
    )


def call_llm(prompt, method_name=None):
    """
    Calls Gemini API with the given prompt.

    If the model hits its token limit mid-output (finish_reason == MAX_TOKENS),
    sends a single continuation turn in the same conversation and concatenates
    the two responses.

    Returns:
        (text: str | None, was_truncated: bool)
        was_truncated is True whenever a MAX_TOKENS truncation was detected,
        regardless of whether the continuation call succeeded.
    """
    try:
        response = _generate(prompt)
        time.sleep(config.API_SLEEP_SEC)

        text = _extract_text(response)

        if _is_max_tokens(response):
            label = method_name or 'unknown'
            print(f"  [TRUNCATED] {label} response was cut off, requesting continuation")

            # Multi-turn continuation: give the model full context so it knows
            # exactly where it stopped.
            user_text = prompt if isinstance(prompt, str) else str(prompt)
            continuation_contents = [
                types.Content(role="user",  parts=[types.Part(text=user_text)]),
                types.Content(role="model", parts=[types.Part(text=text or "")]),
                types.Content(role="user",  parts=[types.Part(text=_CONTINUATION_PROMPT)]),
            ]
            try:
                cont_response = _generate(continuation_contents)
                cont_text = _extract_text(cont_response)
                if cont_text:
                    text = (text or "") + "\n" + cont_text
            except Exception as cont_e:
                print(f"  [TRUNCATED] Continuation call failed: {cont_e}")
                # Fall through — return whatever partial text we have.

            return text, True

        return text, False

    except Exception as e:
        error_str = str(e)

        # Rate limit — wait and retry once
        if '429' in error_str or 'RESOURCE_EXHAUSTED' in error_str:
            print("  Rate limit hit. Waiting 60 seconds...")
            time.sleep(60)
            try:
                response = _generate(prompt)
                return _extract_text(response), False
            except Exception as e2:
                print(f"  Retry failed: {e2}")
                return None, False

        print(f"  API Error FULL DETAILS: {type(e).__name__}: {e}")
        return None, False
