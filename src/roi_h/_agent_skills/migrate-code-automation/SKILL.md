---
name: migrate-code-automation
description: Migrate an existing Python or JavaScript automation codebase into an ROI-H project that uses native tools, durable runs, project skills only when required, evidence, and an immutable automation package. Use when a user asks to import, port, convert, replace, modernize, or move a Playwright, Selenium, Puppeteer, browser, file, PDF, Excel, API, or script-based automation into ROI-H.
---

# Migrate Code Automation

<!-- ROI-H managed agent skill -->

Migrate workflow behavior, not source code structure. Use the old code and its tests as the specification. Use ROI-H for all target project writes and effects.

## 1. Inspect the source

Read the supplied codebase without changing it. Find:

- entry points, triggers, and required arguments;
- ordered business steps and decision rules;
- browser, file, PDF, Excel, API, and external-system actions;
- inputs, outputs, filenames, sheet names, and validation rules;
- secrets and environment variables by name only;
- retries, waits, error handling, and partial-failure behavior; and
- tests, fixtures, or sample outputs that define success.

Ignore framework boilerplate, dependency setup, generated files, and implementation details that do not change the business result. Do not execute or change the legacy automation.

Ask one question only when a missing business rule, credential, source file, account choice, or expected result can materially change the migration.

## 2. Build a migration map

Make a short map from each source behavior to one of these targets:

1. an existing ROI-H operation or built-in tool;
2. a small project-owned skill when no native capability can produce the result; or
3. an explicit unsupported item that blocks equivalent behavior.

Prefer native browser, file verification, PDF, and Excel tools. Do not assume that generic HTTP, shell, structured-data, or arbitrary file tools exist. Treat direct API access as unsupported unless the live catalog provides a specific connector or one narrow project-owned skill can supply it. Do not port Selenium, Playwright, Puppeteer, HTTP clients, shell wrappers, retry frameworks, logging systems, or configuration layers when ROI-H already owns that concern.

Preserve business rules, observable outputs, required approvals, and failure semantics. Do not preserve the old architecture only because it exists.

## 3. Create the development run

Use the native ROI-H bridge when it is available. Otherwise, use the installed `roi-h agent` interface. Read context first, then discover the live operations and their schemas. Do not guess operation arguments.

Reuse a matching unfinished development run. Otherwise, select or create the smallest suitable project and start one durable run in `dev`. Use the phase sequence `explore -> solve -> verify`.

In `explore`, list and inspect relevant native tools. Exercise only the minimum safe reads needed to confirm selectors, source files, formats, and access. Record useful evidence in the run.

## 4. Rebuild with native capabilities

In `solve`, execute the workflow through ROI-H in the required order.

Create a project-owned skill only when the migration map shows a real capability gap. Project-owned tools are Python-only. Reimplement only the missing behavior as one narrow typed tool; do not copy or import untrusted legacy Python or JavaScript. Review generated source before definition. Custom Python is trusted local code: declared network and filesystem access does not confine it. Report the behavior as unsupported when that limit makes the migration unsafe.

Do not add a generic shell, HTTP, arbitrary file, or compatibility-wrapper skill to preserve old code. Follow the installed ROI-H instructions and the live manifest for secrets, approvals, retries, tasks, and other common operating rules.

## 5. Verify equivalence

In `verify`, check the migrated workflow against the source tests or documented success criteria. Verify the business result with native reads, artifact metadata, hashes, workbook read-back, PDF extraction, browser state, or run trace as applicable.

Check at least:

- required inputs map to the new run;
- branch and validation rules produce the same result;
- named outputs and durable artifacts exist;
- secrets are referenced, not embedded;
- no unneeded custom skill replaces an available native tool; and
- the run has no unresolved failure or approval.

If equivalence cannot be shown, do not ship. Report the exact gap and the one action needed to continue.

## 6. Ship and dry-run

Ship one immutable automation only from the verified development run. Verify the package, then run its supported dry-run path. Do not start a production run unless the user explicitly requests it.

Report the source type, migration result, project, environment, development run ID, automation name and version, package verification, dry-run result, artifacts, custom project skills, warnings, and required user action.
