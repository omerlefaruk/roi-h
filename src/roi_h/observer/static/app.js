const ICONS = {
  approval: "/icons/question-line.svg",
  cancelled: "/icons/question-line.svg",
  completed: "/icons/check-line.svg",
  failed: "/icons/error-warning-line.svg",
  idle: "/icons/question-line.svg",
  not_started: "/icons/question-line.svg",
  running: "/icons/loader-4-line.svg",
  skipped: "/icons/question-line.svg",
  unknown: "/icons/question-line.svg",
};

const STATUS_LABELS = {
  approval: "Needs approval",
  cancelled: "Cancelled",
  completed: "Completed",
  failed: "Failed",
  idle: "Idle",
  not_started: "Not started",
  running: "Running",
  skipped: "Skipped",
  unknown: "Unknown outcome",
};

const elements = {
  artifactDownload: document.querySelector("#artifact-download"),
  artifactDrawer: document.querySelector("#artifact-drawer"),
  artifactPreview: document.querySelector("#artifact-preview"),
  artifactProvenance: document.querySelector("#artifact-provenance"),
  attentionFilter: document.querySelector("#attention-filter"),
  drawerClose: document.querySelector("#drawer-close"),
  drawerFileIcon: document.querySelector("#drawer-file-icon"),
  drawerTitle: document.querySelector("#drawer-title"),
  environmentFilter: document.querySelector("#environment-filter"),
  fileDetailsBody: document.querySelector("#file-details-body"),
  projectFilter: document.querySelector("#project-filter"),
  runCount: document.querySelector("#run-count"),
  runDetail: document.querySelector("#run-detail"),
  runList: document.querySelector("#run-list"),
  runSearch: document.querySelector("#run-search"),
  toast: document.querySelector("#toast"),
  workspace: document.querySelector("#workspace"),
};

const state = {
  artifact: null,
  catalog: null,
  detail: null,
  runs: [],
  selectedKey: null,
  toastTimer: null,
};

function escapeHtml(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");
}

function queryString(values) {
  const params = new URLSearchParams();
  Object.entries(values).forEach(([key, value]) => {
    if (value !== null && value !== undefined && value !== "") {
      params.set(key, String(value));
    }
  });
  return params.toString();
}

async function fetchJson(url) {
  const response = await fetch(url, {
    headers: { Accept: "application/json" },
    cache: "no-store",
  });
  const payload = await response.json().catch(() => ({}));
  if (!response.ok) {
    throw new Error(payload.error || `Request failed (${response.status})`);
  }
  return payload;
}

function runKey(run) {
  return `${run.project}/${run.env}/${run.run_id}`;
}

function selectedRun() {
  return state.runs.find((run) => runKey(run) === state.selectedKey) || null;
}

function filteredRuns() {
  const search = elements.runSearch.value.trim().toLowerCase();
  return state.runs.filter((run) => {
    if (elements.attentionFilter.checked && !run.attention) {
      return false;
    }
    if (!search) {
      return true;
    }
    return [
      run.title,
      run.summary,
      run.project,
      run.project_display_name,
      run.env,
      run.run_id,
    ].some((value) => String(value || "").toLowerCase().includes(search));
  });
}

function statusIcon(status) {
  return ICONS[status] || ICONS.idle;
}

function statusLabel(status) {
  return STATUS_LABELS[status] || "Recorded";
}

function formatDate(value, options) {
  const parsed = new Date(value);
  if (Number.isNaN(parsed.getTime())) {
    return "";
  }
  return new Intl.DateTimeFormat(undefined, options).format(parsed);
}

function relativeTime(value) {
  const parsed = new Date(value);
  if (Number.isNaN(parsed.getTime())) {
    return "";
  }
  const seconds = Math.round((parsed.getTime() - Date.now()) / 1000);
  const units = [
    ["year", 31_536_000],
    ["month", 2_592_000],
    ["day", 86_400],
    ["hour", 3_600],
    ["minute", 60],
  ];
  const formatter = new Intl.RelativeTimeFormat(undefined, { numeric: "auto" });
  for (const [unit, size] of units) {
    if (Math.abs(seconds) >= size || unit === "minute") {
      return formatter.format(Math.round(seconds / size), unit);
    }
  }
  return "now";
}

function formatDuration(seconds) {
  if (seconds === null || seconds === undefined) {
    return "—";
  }
  const rounded = Math.max(0, Math.round(Number(seconds)));
  if (rounded < 60) {
    return `${rounded}s`;
  }
  const minutes = Math.floor(rounded / 60);
  const remainder = rounded % 60;
  return remainder ? `${minutes}m ${remainder}s` : `${minutes}m`;
}

function formatBytes(bytes) {
  const value = Number(bytes || 0);
  if (value < 1024) {
    return `${value} B`;
  }
  if (value < 1024 * 1024) {
    return `${(value / 1024).toFixed(value < 10_240 ? 1 : 0)} KB`;
  }
  if (value < 1024 * 1024 * 1024) {
    return `${(value / (1024 * 1024)).toFixed(1)} MB`;
  }
  return `${(value / (1024 * 1024 * 1024)).toFixed(1)} GB`;
}

function dateGroup(value) {
  const parsed = new Date(value);
  if (Number.isNaN(parsed.getTime())) {
    return { key: "unknown", title: "Earlier" };
  }
  const current = new Date();
  const startToday = new Date(current.getFullYear(), current.getMonth(), current.getDate());
  const startDate = new Date(parsed.getFullYear(), parsed.getMonth(), parsed.getDate());
  const days = Math.round((startToday - startDate) / 86_400_000);
  const formatted = formatDate(parsed, { month: "short", day: "numeric", year: "numeric" });
  if (days === 0) {
    return { key: "today", title: `Today — ${formatted}` };
  }
  if (days === 1) {
    return { key: "yesterday", title: `Yesterday — ${formatted}` };
  }
  return { key: formatted, title: formatted };
}

function renderRunSkeletons() {
  elements.runList.innerHTML = `
    <div class="skeleton run-card"></div>
    <div class="skeleton run-card"></div>
    <div class="skeleton run-card"></div>
  `;
  elements.runCount.textContent = "Loading runs…";
}

function renderRuns() {
  const runs = filteredRuns();
  if (!runs.length) {
    elements.runList.innerHTML = `
      <div class="list-empty">
        <h2>No runs found</h2>
        <p>Try changing the filters or search terms.</p>
      </div>
    `;
    elements.runCount.textContent = "Showing 0 runs";
    return;
  }

  const groups = [];
  runs.forEach((run) => {
    const group = dateGroup(run.updated_at);
    let target = groups.find((item) => item.key === group.key);
    if (!target) {
      target = { ...group, runs: [] };
      groups.push(target);
    }
    target.runs.push(run);
  });

  elements.runList.innerHTML = groups
    .map(
      (group) => `
        <section class="run-group">
          <h2 class="run-group-title">${escapeHtml(group.title)}</h2>
          ${group.runs
            .map((run) => {
              const key = runKey(run);
              return `
                <button
                  class="run-card ${key === state.selectedKey ? "selected" : ""}"
                  type="button"
                  data-run-key="${escapeHtml(key)}"
                  aria-pressed="${key === state.selectedKey}"
                >
                  <span class="run-card-status ${escapeHtml(run.status)}">
                    <img src="${statusIcon(run.status)}" alt="" />
                  </span>
                  <span class="run-card-copy">
                    <span class="run-card-title">${escapeHtml(run.title)}</span>
                    <span class="run-card-meta">
                      ${escapeHtml(run.project_display_name)}
                      · ${escapeHtml(String(run.env).toUpperCase())}
                      · ${escapeHtml(relativeTime(run.updated_at))}
                    </span>
                  </span>
                  <img class="run-card-chevron" src="/icons/arrow-right-s-line.svg" alt="" />
                </button>
              `;
            })
            .join("")}
        </section>
      `,
    )
    .join("");

  elements.runList.querySelectorAll("[data-run-key]").forEach((button) => {
    button.addEventListener("click", () => selectRun(button.dataset.runKey));
  });
  elements.runCount.textContent = `Showing ${runs.length} of ${state.runs.length} runs`;
}

function renderDetailLoading() {
  elements.runDetail.innerHTML = `
    <div class="empty-state">
      <h2>Reading run…</h2>
      <p>Building the chronological story from its durable records.</p>
    </div>
  `;
}

function detailSummary(detail) {
  if (detail.status === "failed") {
    const failure = detail.story.find((item) => item.status === "failed");
    if (failure?.error) {
      return `The run stopped during ${failure.title.toLowerCase()}: ${failure.error}`;
    }
  }
  if (detail.status === "unknown") {
    return "The run stopped because a write outcome could not be confirmed safely.";
  }
  if (detail.status === "approval") {
    return "The run is paused until an operator reviews the pending action.";
  }
  if (detail.status === "completed") {
    return `The run completed ${detail.phase_count || detail.step_count} recorded ${
      detail.phase_count ? "phases" : "steps"
    }.`;
  }
  if (detail.status === "running") {
    return "The run is still in progress. This view refreshes automatically.";
  }
  return detail.summary;
}

function renderDetail() {
  const detail = state.detail;
  if (!detail) {
    elements.runDetail.innerHTML = `
      <div class="empty-state">
        <h2>Select a run</h2>
        <p>Choose a run on the left to read what happened.</p>
      </div>
    `;
    return;
  }

  const story = detail.story
    .map((item) => {
      const artifactCards = item.artifacts
        .map((artifact) => renderArtifactCard(detail, item, artifact))
        .join("");
      const steps = item.steps
        .map(
          (step) => `
            <div class="step-row ${step.status === "error" ? "error" : ""}">
              <div class="step-row-head">
                <span class="step-row-name">${escapeHtml(step.name)}</span>
                <span class="step-row-meta">${escapeHtml(step.time)} · ${escapeHtml(step.status)}</span>
              </div>
              ${
                step.error
                  ? `<div class="step-row-error">${escapeHtml(step.error)}</div>`
                  : `<div class="step-row-meta">${escapeHtml(
                      step.duration_seconds == null
                        ? `${step.skill}.${step.tool}`
                        : `${step.skill}.${step.tool} · ${formatDuration(step.duration_seconds)}`,
                    )}</div>`
              }
            </div>
          `,
        )
        .join("");
      const titlePrefix = item.time
        ? `<span class="time">${escapeHtml(item.time)}</span><span>·</span> `
        : "";
      return `
        <li class="timeline-item ${escapeHtml(item.status)}">
          <span class="timeline-marker ${escapeHtml(item.status)}">
            <img src="${statusIcon(item.status)}" alt="" />
          </span>
          <div class="timeline-content">
            <h4 class="timeline-title">
              ${titlePrefix}${escapeHtml(item.title)} ${escapeHtml(statusVerb(item.status))}
            </h4>
            <p class="timeline-description">${escapeHtml(item.description)}</p>
            ${item.error ? `<p class="timeline-error">${escapeHtml(item.error)}</p>` : ""}
            ${artifactCards ? `<div class="artifact-list">${artifactCards}</div>` : ""}
            ${
              steps
                ? `
                  <details class="step-details">
                    <summary>${item.status === "failed" ? "Show failed step" : `Show ${item.steps.length} steps`}</summary>
                    <div class="step-list">${steps}</div>
                  </details>
                `
                : ""
            }
          </div>
        </li>
      `;
    })
    .join("");

  elements.runDetail.innerHTML = `
    <header class="detail-header">
      <h2>${escapeHtml(detail.title)}</h2>
      <div class="status-badge ${escapeHtml(detail.status)}">
        <img src="${statusIcon(detail.status)}" alt="" />
        <span>${escapeHtml(statusLabel(detail.status))}</span>
      </div>
      <p class="detail-summary">${escapeHtml(detailSummary(detail))}</p>
      <div class="detail-meta">
        <span>${escapeHtml(detail.project_display_name)}</span>
        <span class="meta-separator">·</span>
        <span>${escapeHtml(String(detail.env).toUpperCase())}</span>
        <span class="meta-separator">·</span>
        <span>Started ${escapeHtml(formatDate(detail.created_at, {
          hour: "2-digit",
          minute: "2-digit",
        }))}</span>
        <span class="meta-separator">·</span>
        <span>Duration ${escapeHtml(formatDuration(detail.duration_seconds))}</span>
      </div>
      <details class="run-id-details">
        <summary>Show run ID</summary>
        <code>${escapeHtml(detail.run_id)}</code>
      </details>
    </header>
    <section class="story-section">
      <h3>What happened</h3>
      <ol class="timeline">${story}</ol>
      <details class="technical-details">
        <summary>
          <span>
            <span class="technical-title">Technical details</span>
            <span class="technical-subtitle">Steps, invocation IDs, and raw durable records</span>
          </span>
          <img src="/icons/arrow-down-s-line.svg" alt="" />
        </summary>
        <pre class="technical-json">${escapeHtml(JSON.stringify(detail.technical, null, 2))}</pre>
      </details>
    </section>
  `;

  elements.runDetail.querySelectorAll("[data-artifact-path]").forEach((button) => {
    button.addEventListener("click", () => {
      const phaseId = button.dataset.phaseId;
      const phase = detail.story.find((item) => item.id === phaseId);
      const artifact = phase?.artifacts.find(
        (item) => item.relative_path === button.dataset.artifactPath,
      );
      if (artifact && phase) {
        openArtifact(detail, phase, artifact);
      }
    });
  });

  if (window.innerWidth <= 620) {
    elements.runDetail.querySelector(".detail-header")?.addEventListener("click", (event) => {
      if (event.target.closest("details, button, a")) {
        return;
      }
      document.body.classList.remove("detail-visible");
    });
  }
}

function statusVerb(status) {
  return {
    approval: "awaiting approval",
    cancelled: "cancelled",
    completed: "completed",
    failed: "failed",
    idle: "recorded",
    not_started: "not started",
    running: "running",
    skipped: "skipped",
    unknown: "has an unknown outcome",
  }[status] || "recorded";
}

function artifactIcon(artifact) {
  if (artifact.preview_kind === "table") {
    return "/icons/file-excel-2-fill.svg";
  }
  if (artifact.preview_kind === "image") {
    return "/icons/image-line.svg";
  }
  if (artifact.preview_kind === "pdf") {
    return "/icons/file-pdf-2-line.svg";
  }
  return "/icons/file-text-line.svg";
}

function renderArtifactCard(detail, phase, artifact) {
  const preview = artifact.preview_kind !== "unsupported";
  const selected =
    state.artifact?.detailKey === runKey(detail) &&
    state.artifact?.relative_path === artifact.relative_path;
  return `
    <button
      class="artifact-card ${selected ? "selected" : ""}"
      type="button"
      data-artifact-path="${escapeHtml(artifact.relative_path)}"
      data-phase-id="${escapeHtml(phase.id)}"
    >
      <img
        class="artifact-icon ${artifact.preview_kind === "table" ? "excel" : ""}"
        src="${artifactIcon(artifact)}"
        alt=""
      />
      <span>
        <span class="artifact-name">${escapeHtml(artifact.name)}</span>
        <span class="artifact-meta">
          ${escapeHtml(formatBytes(artifact.bytes))}
          · ${preview ? "Preview available" : "Download only"}
        </span>
      </span>
      <span class="artifact-action">${preview ? "Preview" : "Details"}</span>
    </button>
  `;
}

async function loadCatalog() {
  state.catalog = await fetchJson("/api/catalog");
  elements.projectFilter.innerHTML = `
    <option value="">All projects</option>
    ${state.catalog.projects
      .map(
        (project) =>
          `<option value="${escapeHtml(project.name)}">${escapeHtml(project.display_name)}</option>`,
      )
      .join("")}
  `;
}

async function loadRuns({ preserveSelection = true } = {}) {
  renderRunSkeletons();
  const project = elements.projectFilter.value;
  const env = elements.environmentFilter.value;
  const payload = await fetchJson(`/api/runs?${queryString({ project, env })}`);
  state.runs = payload.runs;
  const visible = filteredRuns();
  if (!preserveSelection || !state.runs.some((run) => runKey(run) === state.selectedKey)) {
    state.selectedKey = visible[0] ? runKey(visible[0]) : null;
    state.detail = null;
    closeArtifact();
  }
  renderRuns();
  if (state.selectedKey && !state.detail) {
    await loadSelectedRun();
  }
}

async function selectRun(key) {
  if (!key || key === state.selectedKey) {
    if (window.innerWidth <= 620) {
      document.body.classList.add("detail-visible");
    }
    return;
  }
  state.selectedKey = key;
  state.detail = null;
  closeArtifact();
  renderRuns();
  renderDetailLoading();
  if (window.innerWidth <= 620) {
    document.body.classList.add("detail-visible");
  }
  await loadSelectedRun();
}

async function loadSelectedRun() {
  const run = selectedRun();
  if (!run) {
    renderDetail();
    return;
  }
  renderDetailLoading();
  try {
    state.detail = await fetchJson(
      `/api/run?${queryString({
        project: run.project,
        env: run.env,
        run_id: run.run_id,
      })}`,
    );
    renderDetail();
  } catch (error) {
    elements.runDetail.innerHTML = `
      <div class="empty-state">
        <h2>Could not read this run</h2>
        <p>${escapeHtml(error.message)}</p>
      </div>
    `;
  }
}

function fileUrl(detail, artifact, download = false) {
  return `/api/artifact/file?${queryString({
    project: detail.project,
    env: detail.env,
    run_id: detail.run_id,
    path: artifact.relative_path,
    download: download ? "1" : "",
  })}`;
}

function previewUrl(detail, artifact) {
  return `/api/artifact/preview?${queryString({
    project: detail.project,
    env: detail.env,
    run_id: detail.run_id,
    path: artifact.relative_path,
  })}`;
}

async function openArtifact(detail, phase, artifact) {
  state.artifact = { ...artifact, detailKey: runKey(detail), phase };
  elements.workspace.classList.add("drawer-open");
  elements.artifactDrawer.hidden = false;
  elements.drawerTitle.textContent = artifact.name;
  elements.drawerFileIcon.src = artifactIcon(artifact);
  elements.artifactPreview.innerHTML = '<div class="preview-loading">Preparing preview…</div>';
  elements.artifactDownload.href = fileUrl(detail, artifact, true);
  renderArtifactMetadata(detail, phase, artifact);
  renderDetail();

  try {
    const preview = await fetchJson(previewUrl(detail, artifact));
    if (
      !state.artifact ||
      state.artifact.relative_path !== artifact.relative_path ||
      state.artifact.detailKey !== runKey(detail)
    ) {
      return;
    }
    renderArtifactPreview(detail, artifact, preview);
  } catch (error) {
    elements.artifactPreview.innerHTML = `
      <div class="preview-error">${escapeHtml(error.message)}</div>
    `;
  }
}

function renderArtifactPreview(detail, artifact, preview) {
  if (preview.kind === "table") {
    const rows = preview.rows || [];
    if (!rows.length) {
      elements.artifactPreview.innerHTML =
        '<div class="preview-empty">This table is empty.</div>';
      return;
    }
    const [head, ...body] = rows;
    elements.artifactPreview.innerHTML = `
      <div class="table-preview-wrap">
        <table class="table-preview">
          <thead>
            <tr>${head.map((value) => `<th>${escapeHtml(value ?? "")}</th>`).join("")}</tr>
          </thead>
          <tbody>
            ${body
              .map(
                (row) =>
                  `<tr>${row.map((value) => `<td>${escapeHtml(value ?? "")}</td>`).join("")}</tr>`,
              )
              .join("")}
          </tbody>
        </table>
      </div>
      ${
        preview.truncated
          ? '<p class="preview-footnote">Showing the first 50 rows and 20 columns.</p>'
          : ""
      }
    `;
    return;
  }
  if (preview.kind === "text") {
    elements.artifactPreview.innerHTML = `
      <pre class="text-preview">${escapeHtml(preview.content || "")}</pre>
      ${
        preview.truncated
          ? '<p class="preview-footnote">Preview truncated at 128 KB.</p>'
          : ""
      }
    `;
    return;
  }
  if (preview.kind === "native" && artifact.preview_kind === "image") {
    elements.artifactPreview.innerHTML = `
      <img class="native-image-preview" src="${fileUrl(detail, artifact)}" alt="${escapeHtml(
        artifact.name,
      )}" />
    `;
    return;
  }
  if (preview.kind === "native" && artifact.preview_kind === "pdf") {
    elements.artifactPreview.innerHTML = `
      <iframe class="native-pdf-preview" src="${fileUrl(
        detail,
        artifact,
      )}" title="${escapeHtml(artifact.name)}"></iframe>
    `;
    return;
  }
  elements.artifactPreview.innerHTML = `
    <div class="preview-empty">${escapeHtml(
      preview.message || "Preview is not available for this file type.",
    )}</div>
  `;
}

function renderArtifactMetadata(detail, phase, artifact) {
  elements.artifactProvenance.innerHTML = `
    <div class="provenance-row">
      <span class="provenance-label">Produced during</span>
      <span>${escapeHtml(phase.title)}</span>
    </div>
    <div class="provenance-row">
      <span class="provenance-label">Recorded</span>
      <span>${artifact.registered ? "Yes" : "Run folder only"}</span>
    </div>
    <div class="provenance-row">
      <span class="provenance-label">Modified</span>
      <span>${escapeHtml(
        formatDate(artifact.modified_at, {
          day: "numeric",
          month: "short",
          year: "numeric",
          hour: "2-digit",
          minute: "2-digit",
        }),
      )}</span>
    </div>
    <div class="provenance-row">
      <span class="provenance-label">Size</span>
      <span>${escapeHtml(formatBytes(artifact.bytes))}</span>
    </div>
  `;
  elements.fileDetailsBody.innerHTML = `
    <div><strong>Project:</strong> ${escapeHtml(detail.project_display_name)}</div>
    <div><strong>Environment:</strong> ${escapeHtml(String(detail.env).toUpperCase())}</div>
    <div><strong>Path:</strong> ${escapeHtml(artifact.relative_path)}</div>
    <div><strong>Type:</strong> ${escapeHtml(artifact.mime_type)}</div>
    ${artifact.sha256 ? `<div><strong>SHA-256:</strong> ${escapeHtml(artifact.sha256)}</div>` : ""}
  `;
}

function closeArtifact() {
  state.artifact = null;
  elements.workspace.classList.remove("drawer-open");
  elements.artifactDrawer.hidden = true;
  elements.artifactPreview.replaceChildren();
  if (state.detail) {
    renderDetail();
  }
}

function showToast(message) {
  elements.toast.textContent = message;
  elements.toast.hidden = false;
  window.clearTimeout(state.toastTimer);
  state.toastTimer = window.setTimeout(() => {
    elements.toast.hidden = true;
  }, 3600);
}

async function refreshVisibleRun() {
  if (document.hidden) {
    return;
  }
  const selectedBefore = state.selectedKey;
  try {
    const project = elements.projectFilter.value;
    const env = elements.environmentFilter.value;
    const payload = await fetchJson(`/api/runs?${queryString({ project, env })}`);
    state.runs = payload.runs;
    state.selectedKey = selectedBefore;
    renderRuns();
    if (state.selectedKey) {
      await loadSelectedRun();
    }
  } catch (error) {
    showToast(`Refresh paused: ${error.message}`);
  }
}

function wireEvents() {
  elements.projectFilter.addEventListener("change", () => {
    state.detail = null;
    loadRuns({ preserveSelection: false }).catch((error) => showToast(error.message));
  });
  elements.environmentFilter.addEventListener("change", () => {
    state.detail = null;
    loadRuns({ preserveSelection: false }).catch((error) => showToast(error.message));
  });
  elements.runSearch.addEventListener("input", renderRuns);
  elements.attentionFilter.addEventListener("change", () => {
    renderRuns();
    const visible = filteredRuns();
    if (visible.length && !visible.some((run) => runKey(run) === state.selectedKey)) {
      selectRun(runKey(visible[0])).catch((error) => showToast(error.message));
    }
  });
  elements.drawerClose.addEventListener("click", closeArtifact);
  window.addEventListener("keydown", (event) => {
    if (event.key === "Escape" && state.artifact) {
      closeArtifact();
    }
  });
}

async function init() {
  wireEvents();
  renderRunSkeletons();
  try {
    await loadCatalog();
    await loadRuns({ preserveSelection: false });
    window.setInterval(refreshVisibleRun, 5000);
  } catch (error) {
    elements.runList.innerHTML = `
      <div class="list-empty">
        <h2>Observer unavailable</h2>
        <p>${escapeHtml(error.message)}</p>
      </div>
    `;
    elements.runCount.textContent = "";
    renderDetail();
  }
}

init();
