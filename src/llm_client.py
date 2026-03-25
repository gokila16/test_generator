import time
from openai import OpenAI
import config

client = OpenAI(api_key=config.OPENAI_API_KEY)

def call_llm(prompt):
    """
    Calls OpenAI API with the given prompt.
    Handles rate limits and errors gracefully.
    Returns raw response text or None if failed.
    """
    try:
        response = client.chat.completions.create(
            model=config.LLM_MODEL,
            messages=[{"role": "user", "content": prompt}],
            max_tokens=config.LLM_MAX_TOKENS,
            temperature=config.LLM_TEMPERATURE
        )
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
                response = client.chat.completions.create(
                    model=config.LLM_MODEL,
                    messages=[{"role": "user", "content": prompt}],
                    max_tokens=config.LLM_MAX_TOKENS,
                    temperature=config.LLM_TEMPERATURE
                )
                return response.choices[0].message.content
            except Exception as e2:
                print(f"  Retry failed: {e2}")
                return None

        print(f"  API Error FULL DETAILS: {type(e).__name__}: {e}")
        return None