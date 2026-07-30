import { spawn } from "node:child_process";
import { mkdtemp, rm, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import type { ExtensionAPI, ExtensionContext } from "@earendil-works/pi-coding-agent";
import { StringEnum } from "@earendil-works/pi-ai";
import { Type, type TSchema } from "typebox";

const STATE_TYPE = "roi-h.bridge.state";
const MAX_RESULT_CHARS = 12000;
const MAX_SEARCH_RESULTS = 20;
const DEFAULT_TIMEOUT_MS = 120000;
const SECRET_OPERATION = "secret.set";

interface JsonObject {
	[key: string]: unknown;
}

interface OperationManifest {
	operation_id: string;
	description: string;
	input_schema: JsonObject;
	effect: "read" | "write" | "destructive";
	idempotency: "not_applicable" | "supported" | "required";
	approval_rule: string;
	plan_rule: string;
	execution_mode: "sync" | "task";
	timeout_seconds: number;
}

interface CommandResponse {
	operation: string;
	request_id: string;
	ok: boolean;
	changed: boolean;
	context?: JsonObject;
	result?: JsonObject | null;
	warnings?: string[];
	next_actions?: unknown[];
	error?: JsonObject | null;
}

interface BridgeContext {
	project?: string;
	environment?: "dev" | "prod";
	run_id?: string;
}

interface BridgeState {
	version: 1;
	context: BridgeContext;
	last_task_id?: string;
	last_event_id?: string;
	activated: string[];
}

interface ProcessResult {
	stdout: string;
	stderr: string;
	code: number;
	killed: boolean;
}

const SearchParams = Type.Object({
	query: Type.Optional(Type.String({ description: "Operation name or words to search for" })),
	limit: Type.Optional(
		Type.Integer({ description: "Maximum number of operations to return", minimum: 1, maximum: 20 }),
	),
	activate: Type.Optional(
		Type.Boolean({ description: "Activate the matching operation tools for the next turn" }),
	),
});

const ActivateParams = Type.Object({
	operation: Type.String({ description: "Exact ROI-H operation ID, for example run.start" }),
});

const ContextParams = Type.Object({});

const ExecuteParams = Type.Object({
	operation: Type.String({ description: "Exact ROI-H operation ID" }),
	arguments: Type.Optional(
		Type.Record(Type.String(), Type.Any(), {
			description: "Arguments matching the operation schema returned by roi_h_search",
		}),
	),
	context: Type.Optional(
		Type.Object({
			project: Type.Optional(Type.String()),
			environment: Type.Optional(StringEnum(["dev", "prod"] as const)),
			run_id: Type.Optional(Type.String()),
		}),
	),
	idempotency_key: Type.Optional(
		Type.String({ description: "Stable key for retrying the same write exactly once" }),
	),
});

function emptyState(): BridgeState {
	return { version: 1, context: {}, activated: [] };
}

function normalizeOperation(operation: string): string {
	return operation.trim().toLowerCase();
}

function toolName(operation: string): string {
	return `roi_h_${operation.replace(/[^a-z0-9]+/gi, "_")}`;
}

function operationFromToolName(name: string): string | undefined {
	if (!name.startsWith("roi_h_")) return undefined;
	return name.slice("roi_h_".length).replace(/_/g, ".");
}

function schemaForTool(schema: JsonObject): TSchema {
	const copy = JSON.parse(JSON.stringify(schema)) as JsonObject;
	delete copy.$schema;
	return copy as TSchema;
}

function stableJson(value: unknown): string {
	if (value === null || typeof value !== "object") return JSON.stringify(value);
	if (Array.isArray(value)) return `[${value.map(stableJson).join(",")}]`;
	const object = value as JsonObject;
	return `{${Object.keys(object)
		.sort()
		.map((key) => `${JSON.stringify(key)}:${stableJson(object[key])}`)
		.join(",")}}`;
}

function truncate(text: string, limit = MAX_RESULT_CHARS): string {
	if (text.length <= limit) return text;
	return `${text.slice(0, limit)}\n\n[ROI-H result truncated; use bounded pagination or an artifact reference.]`;
}

function responseText(response: CommandResponse): string {
	return truncate(JSON.stringify(response, null, 2));
}

function responseDetails(response: CommandResponse, operation: string): JsonObject {
	const result = response.result ?? {};
	return {
		operation,
		request_id: response.request_id,
		ok: response.ok,
		changed: response.changed,
		code: response.error && typeof response.error.code === "string" ? response.error.code : undefined,
		task_id: typeof result.task_id === "string" ? result.task_id : undefined,
	};
}

function parseResponse(stdout: string, operation: string): CommandResponse {
	const raw = stdout.trim();
	if (!raw) throw new Error(`ROI-H returned no JSON for ${operation}`);
	const parsed = JSON.parse(raw) as CommandResponse;
	if (!parsed || typeof parsed !== "object" || typeof parsed.ok !== "boolean") {
		throw new Error(`ROI-H returned an invalid response for ${operation}`);
	}
	return parsed;
}

function parseDescribe(stdout: string): OperationManifest[] {
	const response = parseResponse(stdout, "system.describe");
	if (!response.ok || !response.result || !Array.isArray(response.result.operations)) {
		throw new Error(responseText(response));
	}
	return response.result.operations.filter(
		(value): value is OperationManifest =>
			Boolean(value) &&
			typeof value === "object" &&
			typeof (value as OperationManifest).operation_id === "string" &&
			typeof (value as OperationManifest).description === "string" &&
			Boolean((value as OperationManifest).input_schema),
	);
}

function processResultError(result: ProcessResult, operation: string): Error {
	const detail = result.stderr.trim() || result.stdout.trim() || `exit code ${result.code}`;
	return new Error(`ROI-H ${operation} failed: ${truncate(detail, 4000)}`);
}

async function runProcess(
	command: string,
	args: string[],
	cwd: string,
	input: string,
	signal: AbortSignal | undefined,
	timeoutMs: number,
): Promise<ProcessResult> {
	return new Promise((resolve, reject) => {
		const child = spawn(command, args, {
			cwd,
			env: process.env,
			shell: false,
			stdio: ["pipe", "pipe", "pipe"],
		});
		let stdout = "";
		let stderr = "";
		let killed = false;
		let settled = false;
		const timer = setTimeout(() => {
			killed = true;
			child.kill("SIGTERM");
		}, timeoutMs);
		const abort = () => {
			killed = true;
			child.kill("SIGTERM");
		};
		const cleanup = () => {
			clearTimeout(timer);
			signal?.removeEventListener("abort", abort);
		};
		const finish = (result: ProcessResult) => {
			if (settled) return;
			settled = true;
			cleanup();
			resolve(result);
		};

		child.stdout.setEncoding("utf8");
		child.stderr.setEncoding("utf8");
		child.stdout.on("data", (chunk: string) => {
			stdout += chunk;
		});
		child.stderr.on("data", (chunk: string) => {
			stderr += chunk;
		});
		child.on("error", (error) => {
			cleanup();
			if (!settled) {
				settled = true;
				reject(error);
			}
		});
		child.on("close", (code) => {
			finish({ stdout, stderr, code: code ?? 1, killed });
		});
		signal?.addEventListener("abort", abort, { once: true });
		child.stdin.on("error", () => undefined);
		child.stdin.end(input);
	});
}

export default function roiHBridge(pi: ExtensionAPI) {
	const manifests = new Map<string, OperationManifest>();
	const registeredTools = new Set<string>();
	let state = emptyState();
	let currentContext: ExtensionContext | undefined;
	let refreshPromise: Promise<void> | undefined;

	const setStatus = (ctx: ExtensionContext): void => {
		currentContext = ctx;
		const project = state.context.project ?? "no project";
		const environment = state.context.environment ?? "no env";
		const task = state.last_task_id ? ` · task ${state.last_task_id.slice(0, 12)}` : "";
		ctx.ui.setStatus("roi-h", `ROI-H: ${project}/${environment}${task}`);
	};

	const persistState = (): void => {
		pi.appendEntry(STATE_TYPE, state);
		if (currentContext) setStatus(currentContext);
	};

	const restoreState = (ctx: ExtensionContext): void => {
		state = emptyState();
		for (const entry of ctx.sessionManager.getBranch()) {
			if (entry.type !== "custom" || entry.customType !== STATE_TYPE || !entry.data) continue;
			const data = entry.data as Partial<BridgeState>;
			if (data.version !== 1 || !data.context || !Array.isArray(data.activated)) continue;
			state = {
				version: 1,
				context: data.context,
				last_task_id: data.last_task_id,
				last_event_id: data.last_event_id,
				activated: data.activated.filter((value): value is string => typeof value === "string"),
			};
		}
		setStatus(ctx);
	};

	const refreshCatalog = async (ctx?: ExtensionContext): Promise<void> => {
		if (refreshPromise) return refreshPromise;
		refreshPromise = (async () => {
			const result = await runProcess(
				process.env.ROI_H_BIN || "roi-h",
				["agent", "describe"],
				ctx?.cwd ?? process.cwd(),
				"",
				undefined,
				DEFAULT_TIMEOUT_MS,
			);
			if (result.code !== 0) throw processResultError(result, "system.describe");
			const discovered = parseDescribe(result.stdout);
			manifests.clear();
			for (const manifest of discovered) manifests.set(normalizeOperation(manifest.operation_id), manifest);
		})().finally(() => {
			refreshPromise = undefined;
		});
		return refreshPromise;
	};

	const ensureCatalog = async (ctx: ExtensionContext): Promise<void> => {
		if (manifests.size > 0) return;
		await refreshCatalog(ctx);
	};

	const activateOperation = (operation: string): OperationManifest => {
		const normalized = normalizeOperation(operation);
		const manifest = manifests.get(normalized);
		if (!manifest) throw new Error(`Unknown ROI-H operation: ${operation}`);
		const name = toolName(normalized);
		if (!registeredTools.has(name)) {
			registeredTools.add(name);
			pi.registerTool({
				name,
				label: `ROI-H ${normalized}`,
				description: `${manifest.description} Effect: ${manifest.effect}. Execution: ${manifest.execution_mode}.`,
				promptSnippet: `ROI-H ${normalized} (${manifest.effect})`,
				promptGuidelines: [
					`Use exact arguments from the ${normalized} operation schema.`,
					manifest.effect === "destructive"
						? "Follow the operation plan and approval rules. Do not bypass them."
						: "Preserve structured error codes and next actions.",
				],
				parameters: schemaForTool(manifest.input_schema),
				executionMode: manifest.effect === "read" ? "parallel" : "sequential",
				async execute(toolCallId, params, signal, onUpdate, executionContext) {
					return executeOperation(
						normalized,
						(params ?? {}) as JsonObject,
						undefined,
						toolCallId,
						signal,
						onUpdate,
						executionContext,
					);
				},
			});
		}
		if (!state.activated.includes(normalized)) {
			state.activated = [...state.activated, normalized];
			persistState();
		}
		return manifest;
	};

	const executeOperation = async (
		operation: string,
		argumentsValue: JsonObject,
		requestContext: BridgeContext | undefined,
		toolCallId: string,
		signal: AbortSignal | undefined,
		onUpdate: ((update: { content: Array<{ type: "text"; text: string }>; details?: JsonObject }) => void) | undefined,
		ctx: ExtensionContext,
		explicitIdempotencyKey?: string,
	) => {
		await ensureCatalog(ctx);
		const manifest = manifests.get(normalizeOperation(operation));
		if (!manifest) throw new Error(`Unknown ROI-H operation: ${operation}`);

		const mergedContext: BridgeContext = { ...state.context, ...requestContext };
		const stateBefore = stableJson(state);
		let args = { ...argumentsValue };
		let secretInput: string | undefined;
		if (operation === SECRET_OPERATION) {
			if (!ctx.hasUI) throw new Error("secret.set requires interactive user input");
			const name = typeof args.name === "string" ? args.name : "secret";
			secretInput = await ctx.ui.input(`Enter value for ${name}`, "The value is not sent to the model");
			if (secretInput === undefined) throw new Error("secret.set was cancelled");
			delete args.secret_value;
		}

		const requestId = `pi_${ctx.sessionManager.getSessionId()}_${toolCallId}`;
		const key =
			explicitIdempotencyKey ||
			(manifest.effect !== "read" ? `pi-${requestId}` : undefined);
		const request = {
			schema_version: "1.0",
			request_id: requestId,
			...(key ? { idempotency_key: key } : {}),
			context: mergedContext,
			arguments: args,
		};

		const timeoutMs = Math.max(DEFAULT_TIMEOUT_MS, (manifest.timeout_seconds || 30) * 1000);
		const command = process.env.ROI_H_BIN || "roi-h";
		let tempDirectory: string | undefined;
		let commandArgs = ["agent", "call", operation, "--input", "-"];
		let input = JSON.stringify(request);
		if (operation === SECRET_OPERATION) {
			tempDirectory = await mkdtemp(join(tmpdir(), "roi-h-pi-"));
			const requestPath = join(tempDirectory, "request.json");
			await writeFile(requestPath, JSON.stringify(request), { encoding: "utf8", mode: 0o600 });
			commandArgs = ["agent", "call", operation, "--input", requestPath, "--secret-stdin"];
			input = secretInput ?? "";
		}

		try {
			const processResult = await runProcess(command, commandArgs, ctx.cwd, input, signal, timeoutMs);
			if (processResult.killed && signal?.aborted) throw new Error(`ROI-H ${operation} was cancelled`);
			let response: CommandResponse;
			try {
				// ROI-H uses exit code 1 for structured operation failures. Preserve that
				// response instead of replacing it with an untyped process error.
				response = parseResponse(processResult.stdout, operation);
			} catch (error) {
				if (processResult.code !== 0) throw processResultError(processResult, operation);
				throw error;
			}
			const responseResult = response.result ?? {};
			if (typeof responseResult.task_id === "string") state.last_task_id = responseResult.task_id;
			if (typeof responseResult.event_id === "string") state.last_event_id = responseResult.event_id;
			if (typeof responseResult.run_id === "string") state.context.run_id = responseResult.run_id;
			if (typeof responseResult.project === "string") state.context.project = responseResult.project;
			if (responseResult.environment === "dev" || responseResult.environment === "prod") {
				state.context.environment = responseResult.environment;
			}
			if (stableJson(state) !== stateBefore) persistState();
			const text = responseText(response);
			onUpdate?.({ content: [{ type: "text", text }], details: responseDetails(response, operation) });
			return {
				content: [{ type: "text" as const, text }],
				details: responseDetails(response, operation),
			};
		} finally {
			if (tempDirectory) await rm(tempDirectory, { recursive: true, force: true });
		}
	};

	pi.registerTool({
		name: "roi_h_search",
		label: "ROI-H Search",
		description: "Search the complete ROI-H operation catalog and optionally activate exact operation tools.",
		promptSnippet: "Find ROI-H operations before using roi_h_execute",
		parameters: SearchParams,
		async execute(_toolCallId, params, _signal, _onUpdate, ctx) {
			await ensureCatalog(ctx);
			const query = params.query?.trim().toLowerCase() ?? "";
			const limit = Math.min(params.limit ?? 10, MAX_SEARCH_RESULTS);
			const matches = [...manifests.values()]
				.filter((manifest) => {
					if (!query) return true;
					return `${manifest.operation_id} ${manifest.description} ${manifest.effect}`.toLowerCase().includes(query);
				})
				.sort((left, right) => left.operation_id.localeCompare(right.operation_id))
				.slice(0, limit);
			if (params.activate) {
				for (const manifest of matches) activateOperation(manifest.operation_id);
			}
			const result = matches.map((manifest) => ({
				operation: manifest.operation_id,
				description: manifest.description,
				effect: manifest.effect,
				idempotency: manifest.idempotency,
				execution: manifest.execution_mode,
				activated_tool: params.activate ? toolName(manifest.operation_id) : null,
			}));
			return {
				content: [{ type: "text" as const, text: JSON.stringify({ count: result.length, operations: result }, null, 2) }],
				details: { count: result.length, operations: result },
			};
		},
	});

	pi.registerTool({
		name: "roi_h_activate",
		label: "ROI-H Activate",
		description: "Activate one exact ROI-H operation as a typed Pi tool using its live JSON Schema.",
		promptSnippet: "Activate one typed ROI-H operation",
		parameters: ActivateParams,
		async execute(_toolCallId, params, _signal, _onUpdate, ctx) {
			await ensureCatalog(ctx);
			const manifest = activateOperation(params.operation);
			return {
				content: [
					{
						type: "text" as const,
						text: JSON.stringify(
							{ operation: manifest.operation_id, tool: toolName(manifest.operation_id), schema: manifest.input_schema },
							null,
							2,
						),
					},
				],
				details: { operation: manifest.operation_id, tool: toolName(manifest.operation_id) },
			};
		},
	});

	pi.registerTool({
		name: "roi_h_context",
		label: "ROI-H Context",
		description: "Read the selected ROI-H project, environment, health warnings, pending approvals, and safe next actions.",
		promptSnippet: "Inspect current ROI-H context",
		parameters: ContextParams,
		async execute(toolCallId, _params, signal, onUpdate, ctx) {
			return executeOperation("system.context", {}, undefined, toolCallId, signal, onUpdate, ctx);
		},
	});

	pi.registerTool({
		name: "roi_h_execute",
		label: "ROI-H Execute",
		description:
			"Execute any ROI-H operation. Use roi_h_search first for the exact operation schema. This supports reads, writes, approvals, tasks, secrets, plans, and destructive operations through ROI-H's own contract.",
		promptSnippet: "Execute any typed ROI-H operation",
		promptGuidelines: [
			"Use roi_h_search or roi_h_activate before complex calls.",
			"Pass a stable idempotency_key when repeating a write.",
			"Use plan and apply operations for destructive work.",
			"Never invent secret values. secret.set prompts the user directly.",
		],
		parameters: ExecuteParams,
		async execute(toolCallId, params, signal, onUpdate, ctx) {
			return executeOperation(
				params.operation,
				{ ...(params.arguments ?? {}) } as JsonObject,
				params.context,
				toolCallId,
				signal,
				onUpdate,
				ctx,
				params.idempotency_key,
			);
		},
	});

	const commandHelp = "Usage: /roi-h status | refresh | search TEXT | activate OP | project NAME | env dev|prod";
	pi.registerCommand("roi-h", {
		description: "Inspect and configure the ROI-H bridge",
		handler: async (args, ctx) => {
			const [command, ...rest] = args.trim().split(/\s+/).filter(Boolean);
			if (!command || command === "status") {
				await ensureCatalog(ctx);
				ctx.ui.notify(
					`ROI-H: ${manifests.size} operations, ${state.context.project ?? "no project"}/${state.context.environment ?? "no env"}${state.last_task_id ? `, task ${state.last_task_id}` : ""}`,
					"info",
				);
				return;
			}
			if (command === "refresh") {
				await refreshCatalog(ctx);
				ctx.ui.notify(`Loaded ${manifests.size} ROI-H operations`, "info");
				return;
			}
			if (command === "search") {
				await ensureCatalog(ctx);
				const query = rest.join(" ").toLowerCase();
				const matches = [...manifests.values()]
					.filter((manifest) => `${manifest.operation_id} ${manifest.description}`.toLowerCase().includes(query))
					.slice(0, 10)
					.map((manifest) => `${manifest.operation_id} [${manifest.effect}]`)
					.join(", ");
				ctx.ui.notify(matches || "No matching ROI-H operations", "info");
				return;
			}
			if (command === "activate") {
				await ensureCatalog(ctx);
				const manifest = activateOperation(rest[0] ?? "");
				ctx.ui.notify(`Activated ${manifest.operation_id} as ${toolName(manifest.operation_id)}`, "info");
				return;
			}
			if (command === "project" && rest[0]) {
				state.context.project = rest[0];
				persistState();
				ctx.ui.notify(`ROI-H project: ${rest[0]}`, "info");
				return;
			}
			if (command === "env" && (rest[0] === "dev" || rest[0] === "prod")) {
				state.context.environment = rest[0];
				persistState();
				ctx.ui.notify(`ROI-H environment: ${rest[0]}`, "info");
				return;
			}
			ctx.ui.notify(commandHelp, "warning");
		},
	});

	pi.on("session_start", async (_event, ctx) => {
		currentContext = ctx;
		restoreState(ctx);
		try {
			await refreshCatalog(ctx);
			ctx.ui.notify(`ROI-H bridge loaded ${manifests.size} operations`, "info");
		} catch (error) {
			ctx.ui.notify(`ROI-H bridge unavailable: ${error instanceof Error ? error.message : String(error)}`, "warning");
		}
	});

	pi.on("session_tree", async (_event, ctx) => {
		restoreState(ctx);
	});
}

export const __test = {
	stableJson,
	truncate,
	toolName,
	operationFromToolName,
	normalizeOperation,
};
