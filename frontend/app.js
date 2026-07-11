const TOKEN_KEY = "robodata_token";
const ROLE_KEY = "robodata_role";
const SESSION_KEY = "robodata_session_id";
const POLL_INTERVAL_MS = 2000;
const POLL_MAX_RETRIES = 3;

const ACTIVE_STATUSES = new Set(["queued", "running"]);
const TERMINAL_STATUSES = new Set(["completed", "failed", "cancelled"]);

const pollTimers = {};
const pollRetries = {};

/* ------------------------------------------------------------------ */
/* API */
/* ------------------------------------------------------------------ */

function getToken() {
    return localStorage.getItem(TOKEN_KEY);
}

function setToken(token) {
    localStorage.setItem(TOKEN_KEY, token);
}

function clearToken() {
    localStorage.removeItem(TOKEN_KEY);
}

async function apiCall(method, url, body, isFormData) {
    const headers = {};
    const token = getToken();
    if (token) {
        headers["Authorization"] = `Bearer ${token}`;
    }
    if (!isFormData && body !== undefined) {
        headers["Content-Type"] = "application/json";
    }

    const opts = { method, headers };
    if (body) {
        opts.body = isFormData ? body : JSON.stringify(body);
    }

    const res = await fetch(url, opts);
    const data = await res.json().catch(() => null);
    if (!res.ok) {
        const detail = (data && data.detail) ? data.detail : res.statusText;
        const err = new Error(detail);
        err.status = res.status;
        throw err;
    }
    return data;
}

/* ------------------------------------------------------------------ */
/* Auth */
/* ------------------------------------------------------------------ */

async function login(password) {
    const data = await apiCall("POST", "/api/auth/login", { password });
    setToken(data.access_token);
    if (data.role) localStorage.setItem(ROLE_KEY, data.role);
    if (data.judge_session_id) localStorage.setItem(SESSION_KEY, data.judge_session_id);
    return data;
}

function logout() {
    clearToken();
    localStorage.removeItem(ROLE_KEY);
    localStorage.removeItem(SESSION_KEY);
    Object.keys(pollTimers).forEach(id => stopPolling(id));
    showView("login");
}

async function verifyToken() {
    const token = getToken();
    if (!token) return false;
    try {
        const data = await apiCall("GET", "/api/auth/verify");
        if (data.role) localStorage.setItem(ROLE_KEY, data.role);
        if (data.judge_session_id) localStorage.setItem(SESSION_KEY, data.judge_session_id);
        return true;
    } catch {
        clearToken();
        localStorage.removeItem(ROLE_KEY);
        localStorage.removeItem(SESSION_KEY);
        return false;
    }
}

/* ------------------------------------------------------------------ */
/* Download */
/* ------------------------------------------------------------------ */

async function downloadDataset(jobId, filename) {
    try {
        const token = getToken();
        const res = await fetch(`/api/jobs/${jobId}/download`, {
            headers: { "Authorization": `Bearer ${token}` },
        });
        if (!res.ok) {
            const data = await res.json().catch(() => null);
            throw new Error((data && data.detail) || "Download failed");
        }
        const blob = await res.blob();
        const url = URL.createObjectURL(blob);
        const a = document.createElement("a");
        a.href = url;
        a.download = `${filename.replace(/\.[^.]+$/, "")}_dataset.zip`;
        document.body.appendChild(a);
        a.click();
        document.body.removeChild(a);
        URL.revokeObjectURL(url);
    } catch (err) {
        showToast(err.message || "Download failed", "error");
    }
}

/* ------------------------------------------------------------------ */
/* Jobs */
/* ------------------------------------------------------------------ */

async function uploadVideo(file) {
    const formData = new FormData();
    formData.append("video", file);
    return apiCall("POST", "/api/jobs/upload", formData, true);
}

async function refreshJobs() {
    const data = await apiCall("GET", "/api/jobs");
    renderJobs(data.jobs);
    data.jobs.forEach(job => {
        if (ACTIVE_STATUSES.has(job.status)) {
            startPolling(job.job_id);
        }
    });
}

async function deleteJob(jobId) {
    await apiCall("DELETE", `/api/jobs/${jobId}`);
    stopPolling(jobId);
    const card = document.querySelector(`.job-card[data-job-id="${jobId}"]`);
    if (card) {
        card.style.opacity = "0";
        card.style.transform = "translateY(-8px)";
        card.style.transition = "opacity 0.2s, transform 0.2s";
        setTimeout(() => card.remove(), 200);
        setTimeout(checkJobsEmpty, 250);
    }
}

async function fetchJob(jobId) {
    return apiCall("GET", `/api/jobs/${jobId}`);
}

/* ------------------------------------------------------------------ */
/* Polling */
/* ------------------------------------------------------------------ */

function startPolling(jobId) {
    if (pollTimers[jobId]) return;
    pollRetries[jobId] = 0;
    schedulePoll(jobId);
}

function schedulePoll(jobId) {
    pollTimers[jobId] = setTimeout(async () => {
        try {
            const job = await fetchJob(jobId);
            pollRetries[jobId] = 0;
            updateJobCard(job);
            if (TERMINAL_STATUSES.has(job.status)) {
                stopPolling(jobId);
            } else {
                schedulePoll(jobId);
            }
        } catch (err) {
            pollRetries[jobId] = (pollRetries[jobId] || 0) + 1;
            if (pollRetries[jobId] <= POLL_MAX_RETRIES) {
                schedulePoll(jobId);
            } else {
                stopPolling(jobId);
                showToast(`Lost connection for job ${jobId.slice(0, 8)}...`, "error");
            }
        }
    }, POLL_INTERVAL_MS);
}

function stopPolling(jobId) {
    if (pollTimers[jobId]) {
        clearTimeout(pollTimers[jobId]);
        delete pollTimers[jobId];
    }
    delete pollRetries[jobId];
}

function updateSessionBanner() {
    const roleEl = document.getElementById("session-role");
    const idEl = document.getElementById("session-id");
    const role = localStorage.getItem(ROLE_KEY) || "";
    const sid = localStorage.getItem(SESSION_KEY) || "";
    if (roleEl) roleEl.textContent = role;
    if (idEl) idEl.textContent = sid ? sid.slice(0, 8) + "..." : "";
}

/* ------------------------------------------------------------------ */
/* UI — Views */
/* ------------------------------------------------------------------ */

function showView(viewName) {
    document.querySelectorAll(".view").forEach(v => v.classList.remove("active"));
    const target = document.getElementById(`${viewName}-view`);
    if (target) {
        target.classList.add("active");
    }
}

/* ------------------------------------------------------------------ */
/* UI — Toast */
/* ------------------------------------------------------------------ */

function showToast(message, type) {
    let toast = document.querySelector(".toast");
    if (!toast) {
        toast = document.createElement("div");
        toast.className = "toast";
        document.body.appendChild(toast);
    }
    toast.className = `toast ${type || ""}`;
    toast.textContent = message;

    requestAnimationFrame(() => {
        toast.classList.add("visible");
    });

    clearTimeout(toast._timeout);
    toast._timeout = setTimeout(() => {
        toast.classList.remove("visible");
    }, 3500);
}

/* ------------------------------------------------------------------ */
/* UI — Job Cards */
/* ------------------------------------------------------------------ */

function renderJobs(jobs) {
    const list = document.getElementById("jobs-list");
    const existing = new Set();
    list.querySelectorAll(".job-card").forEach(c => existing.add(c.dataset.jobId));

    jobs.forEach(job => {
        if (!existing.has(job.job_id)) {
            addJobCard(job);
        }
    });

    checkJobsEmpty();
}

function addJobCard(job) {
    const template = document.getElementById("job-template");
    const clone = template.content.cloneNode(true);
    const card = clone.querySelector(".job-card");
    card.dataset.jobId = job.job_id;
    populateJobCard(card, job);

    const list = document.getElementById("jobs-list");
    const empty = document.getElementById("jobs-empty");
    if (empty) list.removeChild(empty);

    list.appendChild(card);
}

function updateJobCard(job) {
    const card = document.querySelector(`.job-card[data-job-id="${job.job_id}"]`);
    if (!card) {
        addJobCard(job);
        return;
    }
    populateJobCard(card, job);
}

function populateJobCard(card, job) {
    const status = job.status;
    const pct = Math.round((job.progress || 0) * 100);

    card.querySelector(".job-filename").textContent = job.filename;
    const badge = card.querySelector(".job-status");
    badge.textContent = getStatusLabel(status);
    badge.className = `job-status status-${status}`;

    const stageEl = card.querySelector(".job-stage");
    if (ACTIVE_STATUSES.has(status) && job.current_stage) {
        stageEl.textContent = `Stage: ${job.current_stage}`;
        stageEl.classList.remove("hidden");
    } else if (status === "failed" && job.message) {
        stageEl.textContent = job.message;
        stageEl.classList.remove("hidden");
    } else {
        stageEl.textContent = "";
        stageEl.classList.add("hidden");
    }

    const fill = card.querySelector(".progress-fill");
    fill.style.width = `${pct}%`;
    fill.className = "progress-fill";
    if (status === "failed" || status === "cancelled") {
        fill.classList.add("error");
    } else if (status === "completed") {
        fill.classList.add("complete");
    }

    card.querySelector(".progress-label").textContent = `${pct}%`;

    card.querySelector(".job-time").textContent = `Started: ${formatTime(job.created_at)}`;

    const downloadBtn = card.querySelector(".btn-download");
    if (status === "completed") {
        downloadBtn.classList.remove("hidden");
        downloadBtn.onclick = () => downloadDataset(job.job_id, job.filename);
    } else {
        downloadBtn.classList.add("hidden");
    }

    const deleteBtn = card.querySelector(".btn-delete");
    deleteBtn.onclick = () => deleteJob(job.job_id);
}

function checkJobsEmpty() {
    const list = document.getElementById("jobs-list");
    const hasCards = list.querySelectorAll(".job-card").length > 0;
    const empty = document.getElementById("jobs-empty");

    if (hasCards && empty) {
        list.removeChild(empty);
    } else if (!hasCards && !empty) {
        const div = document.createElement("div");
        div.id = "jobs-empty";
        div.className = "jobs-empty";
        div.innerHTML = "<p>No jobs yet. Upload a video to get started.</p>";
        list.appendChild(div);
    }
}

/* ------------------------------------------------------------------ */
/* Utils */
/* ------------------------------------------------------------------ */

function formatTime(isoString) {
    if (!isoString) return "";
    const then = new Date(isoString);
    const now = new Date();
    const diffMs = now - then;
    const diffSec = Math.floor(diffMs / 1000);

    if (diffSec < 10) return "just now";
    if (diffSec < 60) return `${diffSec} seconds ago`;
    const diffMin = Math.floor(diffSec / 60);
    if (diffMin < 60) return `${diffMin} minute${diffMin > 1 ? "s" : ""} ago`;
    const diffHr = Math.floor(diffMin / 60);
    if (diffHr < 24) return `${diffHr} hour${diffHr > 1 ? "s" : ""} ago`;
    return then.toLocaleDateString("en-US", { month: "short", day: "numeric", hour: "numeric", minute: "2-digit" });
}

function getStatusLabel(status) {
    const map = {
        queued: "Queued",
        running: "Running",
        completed: "Complete",
        failed: "Failed",
        cancelled: "Cancelled",
    };
    return map[status] || status;
}

function getStatusColor(status) {
    const map = {
        queued: "var(--warning)",
        running: "var(--accent)",
        completed: "var(--success)",
        failed: "var(--error)",
        cancelled: "var(--error)",
    };
    return map[status] || "var(--text-dim)";
}

/* ------------------------------------------------------------------ */
/* Drag & Drop */
/* ------------------------------------------------------------------ */

function initDragDrop() {
    const zone = document.getElementById("upload-zone");
    const fileInput = document.getElementById("file-input");
    const uploadBtn = document.getElementById("upload-btn");
    const uploadStatus = document.getElementById("upload-status");
    const uploadFilename = document.getElementById("upload-filename");
    const uploadSpinner = document.getElementById("upload-spinner");
    const uploadSubmitBtn = document.getElementById("upload-submit-btn");
    const uploadClearBtn = document.getElementById("upload-clear-btn");

    let selectedFile = null;

    function showFile(file) {
        selectedFile = file;
        uploadFilename.textContent = file.name;
        uploadStatus.classList.remove("hidden");
        uploadSubmitBtn.classList.remove("hidden");
        uploadClearBtn.classList.remove("hidden");
        uploadSpinner.classList.add("hidden");
    }

    function clearFile() {
        selectedFile = null;
        fileInput.value = "";
        uploadStatus.classList.add("hidden");
        uploadSubmitBtn.classList.add("hidden");
        uploadClearBtn.classList.add("hidden");
        uploadSpinner.classList.add("hidden");
    }

    function validateFile(file) {
        const allowed = ["video/mp4", "video/mov", "video/avi", "video/webm"];
        const byExt = [".mp4", ".mov", ".avi", ".webm"];
        const ext = file.name.slice(file.name.lastIndexOf(".")).toLowerCase();
        if (!allowed.includes(file.type) && !byExt.includes(ext)) {
            showToast("Unsupported file type. Please select MP4, MOV, AVI, or WEBM.", "error");
            return false;
        }
        return true;
    }

    function handleFiles(files) {
        if (files.length === 0) return;
        const file = files[0];
        if (!validateFile(file)) return;
        showFile(file);
    }

    zone.addEventListener("click", () => fileInput.click());
    uploadBtn.addEventListener("click", (e) => {
        e.stopPropagation();
        fileInput.click();
    });

    fileInput.addEventListener("change", () => {
        if (fileInput.files.length > 0) {
            handleFiles(fileInput.files);
        }
    });

    zone.addEventListener("dragover", (e) => {
        e.preventDefault();
        zone.classList.add("dragover");
    });

    zone.addEventListener("dragleave", () => {
        zone.classList.remove("dragover");
    });

    zone.addEventListener("drop", (e) => {
        e.preventDefault();
        zone.classList.remove("dragover");
        handleFiles(e.dataTransfer.files);
    });

    uploadSubmitBtn.addEventListener("click", async () => {
        if (!selectedFile) return;
        uploadSubmitBtn.disabled = true;
        uploadClearBtn.disabled = true;
        uploadSpinner.classList.remove("hidden");

        try {
            const job = await uploadVideo(selectedFile);
            showToast(`Processing "${job.filename}" started!`, "success");
            clearFile();
            addJobCard(job);
            checkJobsEmpty();
            startPolling(job.job_id);
        } catch (err) {
            if (err.status === 409) {
                showToast(
                    "You already have an active job. Please wait for it to complete before uploading a new one.",
                    "error",
                );
            } else {
                showToast(err.message || "Upload failed", "error");
            }
        } finally {
            uploadSubmitBtn.disabled = false;
            uploadClearBtn.disabled = false;
            uploadSpinner.classList.add("hidden");
        }
    });

    uploadClearBtn.addEventListener("click", clearFile);
}

/* ------------------------------------------------------------------ */
/* Init */
/* ------------------------------------------------------------------ */

async function init() {
    const loginForm = document.getElementById("login-form");
    const loginError = document.getElementById("login-error");

    loginForm.addEventListener("submit", async (e) => {
        e.preventDefault();
        const password = document.getElementById("password-input").value;
        loginError.classList.add("hidden");
        const btn = document.getElementById("login-btn");
        btn.disabled = true;
        btn.textContent = "Authenticating...";

        try {
            await login(password);
            document.getElementById("password-input").value = "";
            await onAuthSuccess();
        } catch (err) {
            loginError.textContent = err.message || "Authentication failed";
            loginError.classList.remove("hidden");
        } finally {
            btn.disabled = false;
            btn.textContent = "Authenticate";
        }
    });

    document.getElementById("logout-btn").addEventListener("click", logout);

    initDragDrop();

    const valid = await verifyToken();
    if (valid) {
        await onAuthSuccess();
    }
}

async function onAuthSuccess() {
    showView("dashboard");
    updateSessionBanner();

    const list = document.getElementById("jobs-list");
    list.querySelectorAll(".job-card").forEach(c => c.remove());
    const empty = document.getElementById("jobs-empty");
    if (!empty) {
        const div = document.createElement("div");
        div.id = "jobs-empty";
        div.className = "jobs-empty";
        div.innerHTML = "<p>No jobs yet. Upload a video to get started.</p>";
        list.appendChild(div);
    }

    try {
        await refreshJobs();
    } catch {
        showToast("Failed to load jobs", "error");
    }
}

document.addEventListener("DOMContentLoaded", init);
