"""Run through scripts/dev.py index-sop; never prints credentials."""

import asyncio
import json

from app.rag.service import LocalEmbedding, RagService

if __name__ == "__main__":
    print(
        json.dumps(
            asyncio.run(RagService(embedding=LocalEmbedding(allow_download=True)).index()),
            ensure_ascii=False,
        )
    )
