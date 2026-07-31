---
name: excel
description: Use spreadsheet rows for XLSX or CSV transformation work.
version: 1.0.0
---

Use this skill as guidance when you write a spreadsheet phase module. It is not an
executable tool.

- Use `openpyxl` for XLSX files and the Python `csv` module for CSV files.
- Read source files from `context.input_dir` or `context.reference_dir`.
- Do not change an input file in place. Write a new file with `context.output_path()`.
- Preserve workbook formulas and formats unless the goal requires a change.
- Split large work into read, transform, write, and verify phases when this makes failures
  easier to isolate.
- Return row counts, sheet names, and validation totals in `summary`.
- Return the result workbook or CSV in `artifacts`.
- In a verification phase, reopen the generated file and check its required values.
