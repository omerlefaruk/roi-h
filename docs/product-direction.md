# ROI-H Product Direction

**Status:** Current product focus

## Product promise

ROI-H helps an AI discover a business workflow in development, keeps side effects under
operator approval, and turns the successful run into a versioned automation for safe
production use.

The product value is not the ActiveGraph database. The value is a reliable path from:

```text
explore → solve → verify → immutable automation → audited production run
```

ActiveGraph owns durable runs, authority, decisions, effects, evidence, replay, lineage,
and projections. ROI-H adds only product policy, customer-owned storage, isolated effects,
and package delivery. Production automations use one verified `automation.py:run(context)`
entry point after the restricted runner replaces the current recipe runtime.

## First target workflow

The first supported workflow should be a browser and file workflow:

1. Open a business portal.
2. Find and download a report.
3. Validate or transform the file.
4. Save a named artifact.
5. Publish the workflow.
6. Run the frozen package again in production.

This workflow uses the current browser, files, Excel, PDF, approval, artifact, ship, and
run capabilities. It gives a new operator one clear result to understand.

Connector skills should be added only when they support this workflow or another named
customer workflow. The generic core should stay small.

## Success measures

Track these measures for the target workflow:

- time from install to first successful shipped automation;
- percentage of development runs that ship successfully;
- production success rate;
- number of operator approvals per run;
- percentage of failures resolved by replay or reconciliation; and
- time to diagnose a failed production run.

Do not add platform features only because they are technically interesting. Add them when
they improve one of these measures.

## User language

Lead with **AI-assisted automations** and **safe production runs**. Explain ActiveGraph,
logical paths, and event replay after the user understands the result.

Use these stable terms before version 1.0:

- `project` — an isolated automation domain;
- `run` — one durable execution;
- `skill` — one executable capability;
- `automation` — one immutable production package; and
- `artifact` — one durable output with a digest.

Use `roi-h automation run` for a package execution. Reserve `roi-h run` for a durable run
record when the top-level command layout is introduced.
