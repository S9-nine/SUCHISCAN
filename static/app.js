const EDITABLE_FIELDS = [
  { key: "app", label: "App" },
  { key: "amount", label: "Amount (₹)" },
  { key: "date_time", label: "Date & time" },
  { key: "transaction_id", label: "Transaction ID" },
  { key: "party", label: "Party" },
  { key: "direction", label: "Direction" },
  { key: "status", label: "Status" },
];

let allPayments = [];
let currentDetail = null;
let lastSavedNotes = "";

const $ = (id) => document.getElementById(id);

async function api(path, opts) {
  const res = await fetch(path, opts);
  if (!res.ok) throw new Error(`${path} -> ${res.status}`);
  return res.status === 204 ? null : res.json();
}

// ---------- toasts ----------

function toast(message, type = "success", duration = 5000) {
  const el = document.createElement("div");
  el.className = `toast ${type}`;
  el.innerHTML = `
    <span class="toast-icon">${type === "error" ? "!" : "✓"}</span>
    <span class="toast-message"></span>
    <button class="toast-close" aria-label="Dismiss">&times;</button>
  `;
  el.querySelector(".toast-message").textContent = message; // textContent: preserves \n via CSS white-space, no HTML injection
  const remove = () => {
    el.classList.add("leaving");
    el.addEventListener("animationend", () => el.remove(), { once: true });
  };
  el.querySelector(".toast-close").addEventListener("click", remove);
  if (duration) setTimeout(remove, duration);
  $("toast-container").appendChild(el);
  return el;
}

// ---------- menu view ----------

function computeMetrics(rows) {
  const amounts = rows.map(r => parseFloat(r.amount)).filter(n => !isNaN(n));
  const total = amounts.reduce((a, b) => a + b, 0);
  const needsReview = rows.filter(r => r.needs_review).length;
  const confs = rows.map(r => parseFloat(r.ocr_confidence)).filter(n => !isNaN(n));
  const avgConf = confs.length ? confs.reduce((a, b) => a + b, 0) / confs.length : 0;
  return { count: rows.length, total, needsReview, avgConf };
}

function renderMetrics(rows) {
  const m = computeMetrics(rows);
  $("m-count").textContent = m.count;
  $("m-total").textContent = `₹ ${m.total.toLocaleString("en-IN", { minimumFractionDigits: 2 })}`;
  $("m-review").textContent = m.needsReview;
  $("m-conf").textContent = `${m.avgConf.toFixed(0)}%`;
}

function populateFilterOptions(rows) {
  const fillSelect = (select, values) => {
    const current = select.value;
    select.innerHTML = select.options[0].outerHTML;
    [...new Set(values)].filter(Boolean).sort().forEach(v => {
      const opt = document.createElement("option");
      opt.value = v; opt.textContent = v;
      select.appendChild(opt);
    });
    select.value = current;
  };
  fillSelect($("filter-app"), rows.map(r => r.app));
  fillSelect($("filter-status"), rows.map(r => r.status));
}

function filteredPayments() {
  const app = $("filter-app").value;
  const status = $("filter-status").value;
  const search = $("filter-search").value.trim().toLowerCase();
  return allPayments.filter(r => {
    if (app && r.app !== app) return false;
    if (status && r.status !== status) return false;
    if (search) {
      const hay = `${r.party} ${r.transaction_id}`.toLowerCase();
      if (!hay.includes(search)) return false;
    }
    return true;
  });
}

function renderList() {
  const rows = filteredPayments();
  const list = $("payment-list");
  list.innerHTML = "";
  $("empty-state").classList.toggle("hidden", allPayments.length > 0);

  rows.forEach(r => {
    const conf = parseFloat(r.ocr_confidence) || 0;
    const el = document.createElement("div");
    el.className = "payment-row";
    el.innerHTML = `
      <span class="file">${escapeHtml(r.file)}</span>
      <span class="muted">${escapeHtml(r.app)}</span>
      <span class="muted">₹ ${escapeHtml(r.amount || "?")}</span>
      <span class="muted">${escapeHtml(r.date_time || "?")}</span>
      <span class="confidence-badge ${r.needs_review ? "flagged" : "ok"}">${conf.toFixed(0)}%</span>
      <span class="muted">${r.notes ? "\u{1F4CC}" : ""}${r.needs_review ? " Needs review" : ""}</span>
    `;
    el.addEventListener("click", guarded(() => openDetail(r.id)));
    list.appendChild(el);
  });
}

function escapeHtml(s) {
  return String(s ?? "").replace(/[&<>"']/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
}

async function loadPayments() {
  allPayments = await api("/api/payments");
  populateFilterOptions(allPayments);
  renderMetrics(allPayments);
  renderList();
}

$("payment-list").innerHTML = '<p class="empty-state">Loading…</p>';

// ---------- detail view ----------

function renderDetail(payment) {
  currentDetail = payment;
  $("detail-image").src = payment.screenshot_url || "";
  $("detail-reasons").textContent = payment.review_reasons ? `Flagged: ${payment.review_reasons}` : "";

  const fields = $("detail-fields");
  fields.innerHTML = "";

  const staticRow = document.createElement("div");
  staticRow.className = "field-row";
  staticRow.innerHTML = `<span class="field-label">File</span><span class="field-static">${escapeHtml(payment.file)}</span>`;
  fields.appendChild(staticRow);

  EDITABLE_FIELDS.forEach(({ key, label }) => {
    const row = document.createElement("div");
    row.className = "field-row editable-row";
    row.innerHTML = `
      <span class="field-label">${label}</span>
      <span class="field-value">${escapeHtml(payment[key])}</span>
      <input class="field-input" data-key="${key}" value="${escapeHtml(payment[key])}">
    `;
    fields.appendChild(row);
  });

  const confRow = document.createElement("div");
  confRow.className = "field-row";
  confRow.innerHTML = `<span class="field-label">OCR confidence</span><span class="field-static">${parseFloat(payment.ocr_confidence).toFixed(0)}%</span>`;
  fields.appendChild(confRow);

  lastSavedNotes = payment.notes || "";
  $("detail-notes").value = lastSavedNotes;
  $("notes-status").textContent = "";

  setEditing(false);
}

function setEditing(editing) {
  $("detail-left").classList.toggle("editing", editing);
  $("edit-btn").classList.toggle("hidden", editing);
  $("save-btn").classList.toggle("hidden", !editing);
  $("cancel-btn").classList.toggle("hidden", !editing);
}

async function openDetail(id) {
  const payment = await api(`/api/payments/${id}`);
  renderDetail(payment);
  $("view-menu").classList.add("hidden");
  $("view-detail").classList.remove("hidden");
}

function backToList() {
  $("view-detail").classList.add("hidden");
  $("view-menu").classList.remove("hidden");
  loadPayments();
}

async function saveDetail() {
  const fields = {};
  document.querySelectorAll("#detail-fields .field-input").forEach(input => {
    fields[input.dataset.key] = input.value;
  });
  const updated = await api(`/api/payments/${currentDetail.id}`, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(fields),
  });
  renderDetail(updated);
  toast("Payment updated.");
}

async function saveNotes() {
  const notes = $("detail-notes").value;
  if (notes === lastSavedNotes) return;
  $("notes-status").textContent = "Saving…";
  const updated = await api(`/api/payments/${currentDetail.id}/notes`, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ notes }),
  });
  currentDetail = updated;
  lastSavedNotes = notes;
  $("notes-status").textContent = "Saved";
  setTimeout(() => { if ($("notes-status").textContent === "Saved") $("notes-status").textContent = ""; }, 2000);
}

// ---------- upload ----------

async function uploadFiles(files) {
  if (!files.length) return;
  const formData = new FormData();
  [...files].forEach(f => formData.append("files", f));
  const { added, errors } = await api("/api/upload", { method: "POST", body: formData });
  await loadPayments();
  if (added) toast(`Added ${added} payment${added === 1 ? "" : "s"}. Duplicates were skipped.`);
  if (errors && errors.length) toast(`Couldn't process ${errors.length} file(s):\n${errors.join("\n")}`, "error", 9000);
  if (!added && !errors.length) toast("No new screenshots found in that drop.", "error");
}

// wraps a click handler so a failed request shows a toast instead of failing silently
function guarded(fn) {
  return async (...args) => {
    try {
      await fn(...args);
    } catch (err) {
      toast(`Something went wrong: ${err.message}`, "error");
    }
  };
}

// ---------- wiring ----------

$("browse-btn").addEventListener("click", () => $("file-input").click());
$("file-input").addEventListener("change", guarded((e) => uploadFiles(e.target.files)));

const uploadArea = $("upload-area");
uploadArea.addEventListener("dragover", (e) => { e.preventDefault(); uploadArea.classList.add("dragover"); });
uploadArea.addEventListener("dragleave", () => uploadArea.classList.remove("dragover"));
uploadArea.addEventListener("drop", guarded((e) => {
  e.preventDefault();
  uploadArea.classList.remove("dragover");
  return uploadFiles(e.dataTransfer.files);
}));

$("process-folder-btn").addEventListener("click", guarded(async () => {
  const { added, errors } = await api("/api/process-folder", { method: "POST" });
  await loadPayments();
  if (added) toast(`Added ${added} payment${added === 1 ? "" : "s"} from the screenshots folder.`);
  if (errors && errors.length) toast(`Couldn't process ${errors.length} file(s):\n${errors.join("\n")}`, "error", 9000);
  if (!added && !errors.length) toast("Nothing new to process in the screenshots folder.", "error");
}));

$("export-btn").addEventListener("click", () => { window.location.href = "/api/export"; });

$("filter-app").addEventListener("change", renderList);
$("filter-status").addEventListener("change", renderList);
$("filter-search").addEventListener("input", renderList);

$("back-btn").addEventListener("click", guarded(backToList));
$("edit-btn").addEventListener("click", () => setEditing(true));
$("cancel-btn").addEventListener("click", () => renderDetail(currentDetail));
$("save-btn").addEventListener("click", guarded(saveDetail));
$("detail-notes").addEventListener("blur", guarded(saveNotes));

guarded(loadPayments)();
