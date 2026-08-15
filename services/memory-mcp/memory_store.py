from __future__ import annotations

import time
import uuid
from typing import Any


class MemoryStore:
    """Behavior-compatible implementation of the five tools from mcp_setup.sh."""

    def __init__(self, collection: Any) -> None:
        self.collection = collection

    def remember_memory(
        self,
        text: str,
        title: str | None = None,
        tags: str | None = None,
        source: str | None = None,
    ) -> str:
        text = text.strip()
        if not text:
            return "Memory text is empty; nothing stored."
        memory_id = str(uuid.uuid4())
        now = int(time.time())
        self.collection.add(
            ids=[memory_id],
            documents=[text],
            metadatas=[
                {
                    "title": title or "",
                    "tags": tags or "",
                    "source": source or "",
                    "created_at": now,
                    "updated_at": now,
                }
            ],
        )
        return f"Stored memory_id={memory_id}"

    def search_memory(self, query: str, top_k: int = 5) -> str:
        query = query.strip()
        if not query:
            return "Search query is empty."
        top_k = max(1, min(int(top_k), 10))
        result = self.collection.query(
            query_texts=[query],
            n_results=top_k,
            include=["documents", "metadatas", "distances"],
        )
        ids = (result.get("ids") or [[]])[0]
        docs = (result.get("documents") or [[]])[0]
        metas = (result.get("metadatas") or [[]])[0]
        distances = (result.get("distances") or [[]])[0]
        if not ids:
            return "No relevant memories found."
        chunks = []
        for index, memory_id in enumerate(ids):
            metadata = metas[index] or {}
            chunks.append(
                f"[{index + 1}] id={memory_id}\n"
                f"title={metadata.get('title', '')}\n"
                f"tags={metadata.get('tags', '')}\n"
                f"source={metadata.get('source', '')}\n"
                f"distance={distances[index] if index < len(distances) else ''}\n"
                f"memory:\n{docs[index]}"
            )
        return "\n\n---\n\n".join(chunks)

    def delete_memory(self, memory_id: str) -> str:
        memory_id = memory_id.strip()
        if not memory_id:
            return "memory_id is empty."
        self.collection.delete(ids=[memory_id])
        return f"Deleted memory_id={memory_id}"

    def update_memory(self, memory_id: str, new_text: str) -> str:
        memory_id = memory_id.strip()
        new_text = new_text.strip()
        if not memory_id:
            return "memory_id is empty."
        if not new_text:
            return "new_text is empty."
        current = self.collection.get(ids=[memory_id], include=["metadatas"])
        if not current.get("ids"):
            return f"No memory found with id={memory_id}"
        metadata = (current.get("metadatas") or [{}])[0] or {}
        metadata["updated_at"] = int(time.time())
        self.collection.update(ids=[memory_id], documents=[new_text], metadatas=[metadata])
        return f"Updated memory_id={memory_id}"

    def list_recent_memories(self, limit: int = 10) -> str:
        limit = max(1, min(int(limit), 50))
        result = self.collection.get(include=["documents", "metadatas"])
        rows = []
        for memory_id, document, metadata in zip(
            result.get("ids") or [],
            result.get("documents") or [],
            result.get("metadatas") or [],
            strict=False,
        ):
            metadata = metadata or {}
            rows.append((metadata.get("created_at", 0), memory_id, document, metadata))
        rows.sort(reverse=True, key=lambda row: row[0])
        if not rows:
            return "No memories stored yet."
        output = []
        for created_at, memory_id, document, metadata in rows[:limit]:
            preview = str(document)[:300].replace("\n", " ")
            output.append(
                f"id={memory_id}\n"
                f"title={metadata.get('title', '')}\n"
                f"tags={metadata.get('tags', '')}\n"
                f"created_at={created_at}\n"
                f"preview={preview}"
            )
        return "\n\n---\n\n".join(output)
