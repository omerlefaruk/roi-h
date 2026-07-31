---
name: browser
description: Use browser automation to operate a web user interface.
version: 1.0.0
---

Use this skill as guidance when you write a browser phase module. It is not an executable
tool.

- Use Playwright from the automation phase source.
- Declare all permitted host names in `automation.json` under `network_hosts`.
- Read login values with `context.secret(name)`. Declare each name in `required_secrets`.
- Read run inputs from `context.input_dir` and stable project files from
  `context.reference_dir`.
- Prefer roles, labels, names, and stable test identifiers. Do not depend on screen
  coordinates when a semantic selector exists.
- Wait for a specific page state after each navigation or write.
- Save screenshots, downloads, and extracted evidence with `context.output_path()`.
- Return important files in the phase result `artifacts` map.
- Put independent browser jobs in separate phases only when each job owns its browser
  context and the manifest sets `parallel_safe` to `true`.
- Use a final verification phase that reads prior artifacts and checks the business result.
