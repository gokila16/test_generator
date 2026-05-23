import time
from openai import OpenAI
import config

client = OpenAI(api_key=config.OPENAI_API_KEY)


def _is_reasoning_model(model):
    """gpt-5 family and o-series are reasoning models with a different API contract:
    they require max_completion_tokens (not max_tokens) and only accept the default
    temperature."""
    m = (model or '').lower()
    return m.startswith('gpt-5') or m.startswith('o1') or m.startswith('o3') or m.startswith('o4')


def _build_request_kwargs(prompt):
    model = config.LLM_MODEL
    kwargs = {
        'model': model,
        'messages': [{'role': 'user', 'content': prompt}],
    }
    if _is_reasoning_model(model):
        # Reasoning models: token budget covers reasoning + output; no custom temperature.
        kwargs['max_completion_tokens'] = config.LLM_MAX_TOKENS
        effort = getattr(config, 'LLM_REASONING_EFFORT', None)
        if effort:
            kwargs['reasoning_effort'] = effort
    else:
        kwargs['max_tokens'] = config.LLM_MAX_TOKENS
        kwargs['temperature'] = config.LLM_TEMPERATURE
    return kwargs


def call_llm(prompt):
    """
    Calls OpenAI API with the given prompt.
    Handles rate limits and errors gracefully.
    Returns raw response text or None if failed.
    """
    try:
        response = client.chat.completions.create(**_build_request_kwargs(prompt))
        # Sleep to avoid rate limits
        time.sleep(config.API_SLEEP_SEC)
        return response.choices[0].message.content

    except Exception as e:
        error_str = str(e)

        # Rate limit — wait and retry once
        if '429' in error_str:
            print("  Rate limit hit. Waiting 60 seconds...")
            time.sleep(60)
            try:
                response = client.chat.completions.create(**_build_request_kwargs(prompt))
                return response.choices[0].message.content
            except Exception as e2:
                print(f"  Retry failed: {e2}")
                return None

        print(f"  API Error FULL DETAILS: {type(e).__name__}: {e}")
        return None
