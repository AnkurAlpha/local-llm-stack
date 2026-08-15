from __future__ import annotations

import re

from memory_store import MemoryStore


class FakeCollection:
    def __init__(self) -> None:
        self.rows: dict[str, tuple[str, dict]] = {}

    def add(self, ids, documents, metadatas) -> None:
        self.rows[ids[0]] = (documents[0], metadatas[0])

    def query(self, query_texts, n_results, include):
        items = list(self.rows.items())[:n_results]
        return {
            "ids": [[item[0] for item in items]],
            "documents": [[item[1][0] for item in items]],
            "metadatas": [[item[1][1] for item in items]],
            "distances": [[0.1 for _ in items]],
        }

    def get(self, ids=None, include=None):
        if ids is not None:
            items = [(key, self.rows[key]) for key in ids if key in self.rows]
        else:
            items = list(self.rows.items())
        return {
            "ids": [item[0] for item in items],
            "documents": [item[1][0] for item in items],
            "metadatas": [item[1][1] for item in items],
        }

    def update(self, ids, documents, metadatas) -> None:
        self.rows[ids[0]] = (documents[0], metadatas[0])

    def delete(self, ids) -> None:
        self.rows.pop(ids[0], None)


def test_memory_crud_and_search() -> None:
    collection = FakeCollection()
    store = MemoryStore(collection)
    stored = store.remember_memory("Q4_K_M worked", title="benchmark", tags="llama")
    memory_id = re.search(r"memory_id=(.+)", stored).group(1)
    assert "Q4_K_M worked" in store.search_memory("quantization")
    assert memory_id in store.list_recent_memories()
    assert store.update_memory(memory_id, "Q5_K_M worked") == f"Updated memory_id={memory_id}"
    assert "Q5_K_M worked" in store.search_memory("quantization")
    assert store.delete_memory(memory_id) == f"Deleted memory_id={memory_id}"
    assert store.list_recent_memories() == "No memories stored yet."


def test_memory_inputs_are_bounded() -> None:
    store = MemoryStore(FakeCollection())
    assert store.remember_memory("   ").startswith("Memory text is empty")
    assert store.search_memory("   ").startswith("Search query is empty")
    assert store.delete_memory("   ") == "memory_id is empty."
