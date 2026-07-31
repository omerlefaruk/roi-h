---
name: pdf
description: Use PDF text extraction for a named document workflow.
version: 1.0.0
---

Use this skill as guidance when you write a PDF phase module. It is not an executable
tool.

- Use `pypdf` for text extraction, page inspection, and simple PDF changes.
- Read source PDFs from `context.input_dir` or `context.reference_dir`.
- Write generated text, JSON, or PDF files with `context.output_path()`.
- Keep page numbers and extraction warnings in `summary`.
- Treat empty or image-only pages as a condition that needs explicit handling.
- Put OCR in a separate phase when the environment supplies an OCR dependency.
- Return extracted or generated files in `artifacts`.
- Use a verification phase to check page counts, required text, or output hashes.
