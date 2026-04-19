#!/usr/bin/env python3
"""Azure OpenAI smoke test — validates managed-identity auth for the CRB Phase 3 judge."""

import os
import sys

from azure.identity import DefaultAzureCredential, get_bearer_token_provider
from openai import AzureOpenAI


def main() -> int:
    endpoint = os.getenv("ENDPOINT_URL", "https://aoai-l-eastus2.openai.azure.com/")
    deployment = os.getenv("DEPLOYMENT_NAME", "gpt-5.2")

    token_provider = get_bearer_token_provider(
        DefaultAzureCredential(),
        "https://cognitiveservices.azure.com/.default",
    )

    client = AzureOpenAI(
        azure_endpoint=endpoint,
        azure_ad_token_provider=token_provider,
        api_version="2025-01-01-preview",
    )

    print(f"[smoke] endpoint={endpoint} deployment={deployment}", file=sys.stderr)
    resp = client.chat.completions.create(
        model=deployment,
        messages=[{"role": "user", "content": "Reply with exactly the word: OK"}],
        max_completion_tokens=50,
    )
    text = (resp.choices[0].message.content or "").strip()
    print(text)
    return 0 if "OK" in text.upper() else 1


if __name__ == "__main__":
    sys.exit(main())
