---
description: Research an ROI-H or Pi question using primary sources and save cited findings
argument-hint: "<question>"
---
Research this question: $ARGUMENTS

Use `pi-web-access` for web search and page extraction when available.

Rules:
1. Prefer official documentation, specifications, source code, and first-party repositories.
2. Use `source_check` for important claims when available.
3. Separate observed facts, assumptions, recommendations, and open questions.
4. Include source URLs beside the claims they support.
5. Save one concise Markdown report under `docs/research/` using the repository convention.
6. Do not copy secrets, customer data, or large page dumps into the repository.
7. Ask a `researcher` subagent to cross-check important findings when the question is broad.
