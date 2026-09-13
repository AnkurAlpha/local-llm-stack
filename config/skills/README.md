# Manual behavioral skills

Markdown files in this directory describe behavior and workflows that cannot
be inferred reliably from an MCP tool schema. They are loaded as compact
descriptions initially and their full instructions are added only after the
model activates the skill.

Use YAML front matter with `id`, `description`, and optionally `capability`:

```markdown
---
id: deep-research
description: Produce a careful, source-grounded research result.
capability: internet
---

Behavioral instructions go here.
```
