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

def call_llm(prompt):
    """
    Calls Gemini API with the given prompt.
    Handles rate limits and errors gracefully.
    Returns raw response text or None if failed.
    """
    try:
        response = client.models.generate_content(
            model=config.LLM_MODEL,
            contents=prompt,
            config=types.GenerateContentConfig(
                max_output_tokens=config.LLM_MAX_TOKENS,
                temperature=config.LLM_TEMPERATURE,
            )
        )

        time.sleep(config.API_SLEEP_SEC)
        return response.text

    except Exception as e:
        error_str = str(e)

        # Rate limit — wait and retry once
        if '429' in error_str or 'RESOURCE_EXHAUSTED' in error_str:
            print("  Rate limit hit. Waiting 60 seconds...")
            time.sleep(60)
            try:
                response = client.models.generate_content(
                    model=config.LLM_MODEL,
                    contents=prompt,
                    config=types.GenerateContentConfig(
                        max_output_tokens=config.LLM_MAX_TOKENS,
                        temperature=config.LLM_TEMPERATURE,
                    )
                )
                return response.text
            except Exception as e2:
                print(f"  Retry failed: {e2}")
                return None

        print(f"  API Error FULL DETAILS: {type(e).__name__}: {e}")
        return None
