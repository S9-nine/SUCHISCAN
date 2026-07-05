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
let reviewMode = false;
let reviewQueue = [];
let reviewIndex = 0;
let paletteResults = [];
let paletteActiveIndex = 0;

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
    const cleanup = () => el.remove();
    el.addEventListener("animationend", cleanup, { once: true });
    setTimeout(cleanup, 250); // fallback in case the exit animation never fires; el.remove() twice is a harmless no-op
  };
  el.querySelector(".toast-close").addEventListener("click", remove);
  if (duration) setTimeout(remove, duration);
  $("toast-container").appendChild(el);
  return el;
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

function escapeHtml(s) {
  return String(s ?? "").replace(/[&<>"']/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
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
  $("review-btn").classList.toggle("hidden", m.needsReview === 0);
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
      const hay = `${r.party} ${r.transaction_id} ${r.notes}`.toLowerCase();
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

async function refreshTrashBadge() {
  try {
    const rows = await api("/api/trash");
    const badge = $("trash-count");
    badge.textContent = rows.length;
    badge.classList.toggle("hidden", rows.length === 0);
  } catch { /* non-critical */ }
}

async function loadPayments() {
  allPayments = await api("/api/payments");
  populateFilterOptions(allPayments);
  renderMetrics(allPayments);
  renderList();
  refreshTrashBadge();
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
  reviewMode = false;
  $("review-nav").classList.add("hidden");
  const payment = await api(`/api/payments/${id}`);
  renderDetail(payment);
  $("view-menu").classList.add("hidden");
  $("view-trash").classList.add("hidden");
  $("view-detail").classList.remove("hidden");
}

function backToList() {
  reviewMode = false;
  reviewQueue = [];
  $("review-nav").classList.add("hidden");
  $("view-detail").classList.add("hidden");
  $("view-trash").classList.add("hidden");
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
  toast("Payment updated.");
  if (reviewMode) {
    await advanceReviewAfterResolve();
  } else {
    renderDetail(updated);
  }
}

async function deletePayment() {
  if (!confirm(`Move "${currentDetail.file}" to trash? You can restore it later.`)) return;
  await api(`/api/payments/${currentDetail.id}`, { method: "DELETE" });
  toast("Moved to trash.");
  if (reviewMode) {
    await advanceReviewAfterResolve();
  } else {
    backToList();
  }
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

// ---------- review mode ----------

function enterReviewMode() {
  reviewQueue = allPayments.filter(p => p.needs_review).map(p => p.id);
  if (!reviewQueue.length) { toast("No flagged payments to review.", "error"); return; }
  reviewMode = true;
  reviewIndex = 0;
  $("review-nav").classList.remove("hidden");
  showReviewItem();
}

async function showReviewItem() {
  const id = reviewQueue[reviewIndex];
  const payment = await api(`/api/payments/${id}`);
  renderDetail(payment);
  $("view-menu").classList.add("hidden");
  $("view-detail").classList.remove("hidden");
  $("review-counter").textContent = `${reviewIndex + 1} of ${reviewQueue.length} flagged`;
  $("review-prev-btn").disabled = reviewIndex === 0;
  $("review-next-btn").disabled = reviewIndex === reviewQueue.length - 1;
}

function reviewGoto(index) {
  if (index < 0 || index >= reviewQueue.length) return;
  reviewIndex = index;
  guarded(showReviewItem)();
}

function exitReviewMode() {
  backToList();
}

async function advanceReviewAfterResolve() {
  reviewQueue.splice(reviewIndex, 1);
  await loadPayments();
  if (!reviewQueue.length) {
    toast("All caught up! No more flagged payments.");
    exitReviewMode();
    return;
  }
  if (reviewIndex >= reviewQueue.length) reviewIndex = reviewQueue.length - 1;
  showReviewItem();
}

// ---------- trash ----------

async function openTrash() {
  const rows = await api("/api/trash");
  renderTrashList(rows);
  $("view-menu").classList.add("hidden");
  $("view-trash").classList.remove("hidden");
}

function closeTrash() {
  $("view-trash").classList.add("hidden");
  $("view-menu").classList.remove("hidden");
  loadPayments();
}

function renderTrashList(rows) {
  const list = $("trash-list");
  list.innerHTML = "";
  $("trash-empty").classList.toggle("hidden", rows.length > 0);

  rows.forEach(r => {
    const el = document.createElement("div");
    el.className = "payment-row";
    el.innerHTML = `
      <span class="file">${escapeHtml(r.file)}</span>
      <span class="muted">${escapeHtml(r.app)}</span>
      <span class="muted">₹ ${escapeHtml(r.amount || "?")}</span>
      <span class="muted">${escapeHtml(r.date_time || "?")}</span>
      <span></span>
      <div class="trash-row-actions">
        <button class="secondary restore-btn">Restore</button>
        <button class="danger purge-btn">Delete forever</button>
      </div>
    `;
    el.querySelector(".restore-btn").addEventListener("click", guarded(async (e) => {
      e.stopPropagation();
      await api(`/api/payments/${r.id}/restore`, { method: "POST" });
      toast("Restored.");
      openTrash();
    }));
    el.querySelector(".purge-btn").addEventListener("click", guarded(async (e) => {
      e.stopPropagation();
      if (!confirm(`Permanently delete "${r.file}"? This can't be undone.`)) return;
      await api(`/api/payments/${r.id}/purge`, { method: "DELETE" });
      toast("Deleted forever.");
      openTrash();
    }));
    list.appendChild(el);
  });
}

// ---------- command palette ----------

const PALETTE_ACTIONS = [
  { title: "Upload screenshots", hint: "action", run: () => { closePalette(); $("file-input").click(); } },
  { title: "Process screenshots folder", hint: "action", run: () => { closePalette(); $("process-folder-btn").click(); } },
  { title: "Toggle dark mode", hint: "action", run: () => { closePalette(); toggleTheme(); } },
  { title: "Open trash", hint: "action", run: () => { closePalette(); guarded(openTrash)(); } },
  { title: "Download Excel export", hint: "action", run: () => { closePalette(); window.location.href = "/api/export"; } },
  { title: "Review flagged payments", hint: "action", run: () => { closePalette(); enterReviewMode(); } },
];

function openPalette() {
  $("palette-overlay").classList.remove("hidden");
  $("palette-input").value = "";
  renderPaletteResults("");
  setTimeout(() => $("palette-input").focus(), 0);
}

function closePalette() {
  $("palette-overlay").classList.add("hidden");
}

function renderPaletteResults(query) {
  const q = query.trim().toLowerCase();
  const paymentMatches = !q ? [] : allPayments.filter(p =>
    `${p.file} ${p.app} ${p.party} ${p.transaction_id}`.toLowerCase().includes(q)
  ).slice(0, 8).map(p => ({
    title: `${p.party || p.file} — ₹${p.amount || "?"}`,
    hint: p.app || "payment",
    run: () => { closePalette(); guarded(() => openDetail(p.id))(); },
  }));
  const actionMatches = PALETTE_ACTIONS.filter(a => !q || a.title.toLowerCase().includes(q));
  paletteResults = [...paymentMatches, ...actionMatches];
  paletteActiveIndex = 0;

  const container = $("palette-results");
  container.innerHTML = "";
  if (!paletteResults.length) {
    container.innerHTML = '<div class="palette-empty">No matches.</div>';
    return;
  }
  paletteResults.forEach((r, i) => {
    const el = document.createElement("div");
    el.className = `palette-item${i === 0 ? " active" : ""}`;
    el.innerHTML = `<span class="palette-title">${escapeHtml(r.title)}</span><span class="palette-hint">${escapeHtml(r.hint)}</span>`;
    el.addEventListener("click", r.run);
    el.addEventListener("mouseenter", () => setPaletteActive(i));
    container.appendChild(el);
  });
}

function setPaletteActive(i) {
  paletteActiveIndex = i;
  [...$("palette-results").children].forEach((el, idx) => el.classList.toggle("active", idx === i));
}

// ---------- dark mode ----------

function isDark() {
  const attr = document.documentElement.dataset.theme;
  if (attr) return attr === "dark";
  return window.matchMedia("(prefers-color-scheme: dark)").matches;
}

function updateThemeIcon() {
  const dark = isDark();
  $("theme-icon-dark").classList.toggle("hidden", dark);
  $("theme-icon-light").classList.toggle("hidden", !dark);
}

function setTheme(mode) {
  if (mode) {
    document.documentElement.dataset.theme = mode;
    localStorage.setItem("suchiscan-theme", mode);
  } else {
    delete document.documentElement.dataset.theme;
    localStorage.removeItem("suchiscan-theme");
  }
  updateThemeIcon();
}

function toggleTheme() {
  setTheme(isDark() ? "light" : "dark");
}

function initTheme() {
  const saved = localStorage.getItem("suchiscan-theme");
  if (saved) document.documentElement.dataset.theme = saved;
  updateThemeIcon();
}

// ---------- shortcuts help ----------

function openShortcuts() { $("shortcuts-overlay").classList.remove("hidden"); }
function closeShortcuts() { $("shortcuts-overlay").classList.add("hidden"); }

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

// ---------- keyboard shortcuts ----------

document.addEventListener("keydown", (e) => {
  const paletteOpen = !$("palette-overlay").classList.contains("hidden");
  const shortcutsOpen = !$("shortcuts-overlay").classList.contains("hidden");

  if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === "k") {
    e.preventDefault();
    paletteOpen ? closePalette() : openPalette();
    return;
  }

  if (paletteOpen) { if (e.key === "Escape") closePalette(); return; }
  if (shortcutsOpen) { if (e.key === "Escape") closeShortcuts(); return; }

  if (e.key === "Escape") {
    if (!$("view-detail").classList.contains("hidden")) {
      reviewMode ? exitReviewMode() : backToList();
    } else if (!$("view-trash").classList.contains("hidden")) {
      closeTrash();
    }
    return;
  }

  const tag = document.activeElement.tagName;
  if (tag === "INPUT" || tag === "TEXTAREA" || tag === "SELECT") return;

  if (e.key === "/") { e.preventDefault(); $("filter-search").focus(); }
  else if (e.key.toLowerCase() === "d") { toggleTheme(); }
  else if (e.key === "?") { openShortcuts(); }
  else if (reviewMode && !$("view-detail").classList.contains("hidden")) {
    if (e.key === "ArrowLeft") { e.preventDefault(); reviewGoto(reviewIndex - 1); }
    else if (e.key === "ArrowRight") { e.preventDefault(); reviewGoto(reviewIndex + 1); }
  }
});

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
$("delete-btn").addEventListener("click", guarded(deletePayment));
$("detail-notes").addEventListener("blur", guarded(saveNotes));

$("review-btn").addEventListener("click", guarded(enterReviewMode));
$("review-prev-btn").addEventListener("click", () => reviewGoto(reviewIndex - 1));
$("review-next-btn").addEventListener("click", () => reviewGoto(reviewIndex + 1));
$("review-exit-btn").addEventListener("click", guarded(exitReviewMode));

$("trash-btn").addEventListener("click", guarded(openTrash));
$("trash-back-btn").addEventListener("click", guarded(closeTrash));

$("palette-btn").addEventListener("click", openPalette);
$("palette-input").addEventListener("input", (e) => renderPaletteResults(e.target.value));
$("palette-input").addEventListener("keydown", (e) => {
  if (e.key === "ArrowDown") { e.preventDefault(); setPaletteActive(Math.min(paletteActiveIndex + 1, paletteResults.length - 1)); }
  else if (e.key === "ArrowUp") { e.preventDefault(); setPaletteActive(Math.max(paletteActiveIndex - 1, 0)); }
  else if (e.key === "Enter") { e.preventDefault(); paletteResults[paletteActiveIndex]?.run(); }
});
$("palette-overlay").addEventListener("click", (e) => { if (e.target.id === "palette-overlay") closePalette(); });

$("theme-btn").addEventListener("click", toggleTheme);
$("help-btn").addEventListener("click", openShortcuts);
$("shortcuts-close-btn").addEventListener("click", closeShortcuts);
$("shortcuts-overlay").addEventListener("click", (e) => { if (e.target.id === "shortcuts-overlay") closeShortcuts(); });

initTheme();
guarded(loadPayments)();
