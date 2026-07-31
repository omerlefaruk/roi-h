# ROI-H Product Direction

**Status:** Current product contract

## Product promise

ROI-H helps an AI build a business automation in development, prove its result, ship the
exact verified source, and run that immutable package in production.

```text
guidance -> modular source -> development run -> verification -> immutable package -> production run
```

ActiveGraph is the authority for run state, phase state, input evidence, artifact evidence,
source identity, completion, and failure. ROI-H does not keep a second run event log.

## Stable terms

- `project`: one isolated customer automation domain.
- `run`: one durable execution recorded in ActiveGraph.
- `skill`: Markdown guidance for an AI. A skill does not execute code.
- `automation`: modular Python source in development or one immutable production package.
- `phase`: one small Python module with declared dependencies and one attempt boundary.
- `artifact`: one durable phase output with an identity and digest.

## Automation source

An editable source contains `automation.json`, small phase modules, and optional shared
`lib/` modules. It does not contain one large generated script.

The manifest declares:

- phase module names;
- dependency edges in `needs`;
- verification roles;
- phases that are safe to run in parallel;
- the maximum parallel phase count;
- required secret names; and
- permitted network host names for policy and review.

The AI can use standard Python and installed libraries. ROI-H maintainers do not supply or
update hard-coded action scripts. Guidance skills explain safe patterns for browser, file,
Excel, and PDF work.

## Development and production

`automation.dev.run` freezes the current source before the first phase starts. The runner
executes dependency-ready phases and can run declared independent phases in parallel.
Each phase uses its own read-only source copy, work directory, and output directory. The
runner checks source digests before and after each execution wave. Artifacts connect phases.
One manifest can contain at most 32 phases. Each phase timeout and the public operation
timeout give the caller a bounded execution contract.

At least one verification phase is required. A failed or blocked verification phase makes
the run fail. `automation.ship` accepts only the frozen source digest from a completed
development run with successful verification evidence.

`automation.run` works only in production. It verifies the package digest and source digest
before it starts a new ActiveGraph run. It also compares the production run snapshot with
the verified package source digest. Later edits to development source cannot change a
shipped package. A run ID is reserved once and cannot be reused.

## First target workflow

The first target remains a browser and file workflow:

1. Open a business portal.
2. Download a report.
3. Validate or transform the file.
4. Save named artifacts.
5. Verify the business result.
6. Ship the verified source.
7. Run the immutable package in production when the user requests it.

The AI writes the required phase modules from guidance. ROI-H owns durability, evidence,
source freezing, dependency scheduling, secrets, artifacts, and package identity.
