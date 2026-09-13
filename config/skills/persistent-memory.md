---
id: persistent-memory
description: Decide when to search, store, update, or delete durable user memory.
capability: memory
---

# Persistent memory behavior

Use the memory capability deliberately. Search it when an earlier decision,
preference, project fact, or continuing task may affect the answer. Store only
useful long-term information: stable preferences, decisions, important facts,
reusable commands, and meaningful project state.

Do not store passwords, tokens, private credentials, short-lived conversation
details, or sensitive information unless the user explicitly asks for it.
Before storing, search for a likely duplicate. Update an existing memory when
the new information supersedes it, and delete an entry when the user asks or it
is no longer appropriate. Use exact memory IDs for updates and deletion.
