# LLM Adapter

The adapter calls an OpenAI-compatible Chat Completions endpoint. Configuration
comes from the repository-root `.env` file or existing environment variables:

```env
LLM_BASE_URL=https://your-relay.example.com/v1
LLM_API_KEY=replace-with-your-api-key
LLM_MODEL_ID=replace-with-the-relay-model-id
```

Environment variables take precedence over values in `.env`. Only `base_url`
and `model_id` may be written to run manifests; the API key is never serialized.
