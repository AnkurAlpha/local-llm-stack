from __future__ import annotations

import os
import sys
import time

import chromadb
from chromadb.utils import embedding_functions
from mcp.server.fastmcp import FastMCP
from memory_store import MemoryStore


def connect_with_retry() -> object:
    host = os.getenv("CHROMA_HOST", "chroma")
    port = int(os.getenv("CHROMA_PORT", "8000"))
    collection_name = os.getenv("CHROMA_COLLECTION", "agent_memory")
    last_error: Exception | None = None
    for attempt in range(1, 91):
        try:
            client = chromadb.HttpClient(host=host, port=port)
            client.heartbeat()
            return client.get_or_create_collection(
                name=collection_name,
                metadata={"hnsw:space": "cosine"},
                embedding_function=embedding_functions.DefaultEmbeddingFunction(),
            )
        except Exception as exc:
            last_error = exc
            print(
                f"Chroma not ready (attempt {attempt}/90): {type(exc).__name__}", file=sys.stderr, flush=True
            )
            time.sleep(2)
    raise RuntimeError("Chroma remained unavailable") from last_error


mcp = FastMCP("chroma-memory")
store = MemoryStore(connect_with_retry())


@mcp.tool()
def remember_memory(
    text: str,
    title: str | None = None,
    tags: str | None = None,
    source: str | None = None,
) -> str:
    """Store useful long-term memory: results, decisions, commands, facts, or preferences."""
    return store.remember_memory(text, title, tags, source)


@mcp.tool()
def search_memory(query: str, top_k: int = 5) -> str:
    """Search long-term memory for information relevant to the current task."""
    return store.search_memory(query, top_k)


@mcp.tool()
def delete_memory(memory_id: str) -> str:
    """Delete one memory by exact memory_id."""
    return store.delete_memory(memory_id)


@mcp.tool()
def update_memory(memory_id: str, new_text: str) -> str:
    """Replace an existing memory by exact memory_id."""
    return store.update_memory(memory_id, new_text)


@mcp.tool()
def list_recent_memories(limit: int = 10) -> str:
    """List recently stored memories."""
    return store.list_recent_memories(limit)


if __name__ == "__main__":
    mcp.run()
