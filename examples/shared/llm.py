"""Package-local model client; Controller and Actions use the same .env settings."""
import json
import os
from openai import OpenAI


def complete(system, data):
    with OpenAI(api_key=os.environ["OPENAI_API_KEY"],
                base_url=os.getenv("OPENAI_BASE_URL") or "https://api.openai.com/v1",
                timeout=120, max_retries=0) as client:
        response = client.chat.completions.create(
            model=os.environ["OPENAI_MODEL"],
            reasoning_effort=(os.getenv("OPENAI_REASONING_EFFORT") or "high").strip() or "high",
            messages=[{"role": "system", "content": system},
                      {"role": "user", "content": json.dumps(data, ensure_ascii=False)}])
    text = response.choices[0].message.content
    if not text or not text.strip():
        raise ValueError("模型未返回正文")
    return text.strip()


def structured(system, data):
    text = complete(system + '\n只返回 JSON 对象，不加 Markdown 围栏。', data)
    if text.startswith('```'):
        text = text.split('\n', 1)[1].rsplit('```', 1)[0].strip()
    return json.loads(text)
