---
name: files
description: Use logical files for RPA input, work, output, and artifact preparation.
version: 1.0.0
---

Use this skill as guidance for file work in a modular automation. It is not an executable
tool.

- Treat `context.input_dir` and `context.reference_dir` as read-only sources.
- Use `context.work_dir` for temporary state for one phase attempt.
- Use `context.output_path(relative)` for files that the phase will return.
- Read dependency artifacts from `context.dependencies[phase_id][artifact_name]`.
- Do not use absolute customer paths in source, manifests, results, or ActiveGraph events.
- Use standard-library file, archive, JSON, CSV, hashing, and text functions first.
- Return durable files in `artifacts` and small evidence values in `summary`.
- Use a verification phase to reopen and check produced files.
