"""Optional LLM adapter for BatCode agent.

This module tries to use OpenAI if `OPENAI_API_KEY` is set. If not available,
functions return a structured fallback response so the rest of the agent can work.
"""
import os
import logging

logger = logging.getLogger(__name__)


def generate_with_llm(prompt: str, model: str = None, temperature: float = 0.2, max_tokens: int = 800):
    """Call OpenAI ChatCompletion if available.

    Returns a dict: {'ok': bool, 'text': str, 'meta': {...}} or {'ok': False, 'error': str}
    """
    model = model or os.environ.get('OPENAI_MODEL', 'gpt-3.5-turbo')
    api_key = os.environ.get('OPENAI_API_KEY')
    if not api_key:
        return {'ok': False, 'error': 'OPENAI_API_KEY not set', 'text': None}

    try:
        import openai
    except Exception as exc:
        return {'ok': False, 'error': f'openai package not installed: {exc}', 'text': None}

    try:
        openai.api_key = api_key
        resp = openai.ChatCompletion.create(
            model=model,
            messages=[
                {"role": "system", "content": "You are a helpful coding assistant that summarizes instructions and suggests a small project scaffold."},
                {"role": "user", "content": prompt},
            ],
            temperature=temperature,
            max_tokens=max_tokens,
        )
        text = resp['choices'][0]['message']['content'].strip()
        return {'ok': True, 'text': text, 'meta': {'model': model}}
    except Exception as exc:
        logger.exception('LLM call failed')
        return {'ok': False, 'error': str(exc), 'text': None}
