"use client";

import React, { useState, useEffect, useRef } from "react";
import { UploadCloud, X, LayoutGrid, FileText, CheckCircle2, ChevronRight, LogOut, Loader2, Sparkles, HelpCircle, MessageSquare } from "lucide-react";
import JobCard from "../components/JobCard";
import VideoInspector from "../components/VideoInspector";
import TrajectoryChart from "../components/TrajectoryChart";
import ReviewPanel from "../components/ReviewPanel";
import AssistantChat from "../components/AssistantChat";
import OrchestratorPanel from "../components/OrchestratorPanel";

const DEMO_TOKEN = "demo-local";
const POLL_INTERVAL_MS = 2000;

export default function Home() {
  const [token, setToken] = useState<string>(DEMO_TOKEN);
  const [role, setRole] = useState<string>("local demo");
  const [sessionId, setSessionId] = useState<string>("shared");
  const [isAuthChecking, setIsAuthChecking] = useState(false);

  // Dashboard states
  const [jobs, setJobs] = useState<any[]>([]);
  const [selectedFile, setSelectedFile] = useState<File | null>(null);
  const [isUploading, setIsUploading] = useState(false);
  const [uploadProgress, setUploadProgress] = useState<number | null>(null);
  const [dragActive, setDragActive] = useState(false);

  // Details View states
  const [selectedJobId, setSelectedJobId] = useState<string | null>(null);
  const [activeJob, setActiveJob] = useState<any | null>(null);
  const [reviewsData, setReviewsData] = useState<any | null>(null);
  const [playbackTime, setPlaybackTime] = useState(0);
  const [playbackDuration, setPlaybackDuration] = useState(0);
  const [scrubTriggerTime, setScrubTriggerTime] = useState<number | null>(null);

  // Verdict state for salvage path warning banner
  const [verdicts, setVerdicts] = useState<{ pose: string | null; retarget: string | null }>({
    pose: null,
    retarget: null,
  });

  const [modalRightTab, setModalRightTab] = useState<"reports" | "chat" | "orchestration">("reports");

  const pollTimers = useRef<{ [key: string]: NodeJS.Timeout }>({});
  const fileInputRef = useRef<HTMLInputElement>(null);
  const selectedJobIdRef = useRef<string | null>(null);

  useEffect(() => {
    selectedJobIdRef.current = selectedJobId;
  }, [selectedJobId]);

  const normalizeReviewsData = (data: any, jobId: string) => {
    const byStage: Record<string, any> = { job_id: jobId };
    for (const review of data?.reviews || []) {
      if (review?.review_stage) {
        byStage[review.review_stage] = review;
      }
    }
    return byStage;
  };

  const fetchReviewsForJob = async (jobId: string, authToken: string) => {
    try {
      const res = await fetch(`/api/jobs/${jobId}/reviews`, {
        headers: { Authorization: `Bearer ${authToken}` },
      });
      if (!res.ok) return null;
      const data = await res.json();
      const normalized = normalizeReviewsData(data, jobId);
      setReviewsData(normalized);
      return normalized;
    } catch {
      return null;
    }
  };

  useEffect(() => {
    void fetchJobs(DEMO_TOKEN);

    return () => {
      Object.values(pollTimers.current).forEach(clearTimeout);
    };
  }, []);

  const fetchJobDetailsDirectly = async (jobId: string, authToken: string) => {
    try {
      const res = await fetch(`/api/jobs/${jobId}`, {
        headers: { Authorization: `Bearer ${authToken}` },
      });
      if (res.ok) {
        const job = await res.json();
        setActiveJob(job);
        
        await fetchReviewsForJob(jobId, authToken);

        if (job.status === "running" || job.status === "queued") {
          startPolling(jobId, authToken);
        }
      } else {
        window.history.pushState(null, "", "/");
        setSelectedJobId(null);
        setActiveJob(null);
      }
    } catch {
      window.history.pushState(null, "", "/");
      setSelectedJobId(null);
      setActiveJob(null);
    }
  };

  useEffect(() => {
    const handleLocationChange = () => {
      const path = window.location.pathname;
      const match = path.match(/^\/jobs\/([a-zA-Z0-9_-]+)$/);
      if (match) {
        const jobId = match[1];
        setSelectedJobId(jobId);
        
        const job = jobs.find((j) => j.job_id === jobId);
        if (job) {
          setActiveJob(job);
          if (!reviewsData || reviewsData.job_id !== jobId) {
            void fetchReviewsForJob(jobId, token!);
          }
        } else {
          fetchJobDetailsDirectly(jobId, token);
        }
      } else {
        setSelectedJobId(null);
        setActiveJob(null);
        setReviewsData(null);
      }
    };

    window.addEventListener("popstate", handleLocationChange);
    if (token) {
      handleLocationChange();
    }

    return () => window.removeEventListener("popstate", handleLocationChange);
  }, [jobs, token]);

  const handleLogout = () => {
    setJobs([]);
    setSelectedJobId(null);
    setActiveJob(null);
    setReviewsData(null);
    setVerdicts({ pose: null, retarget: null });

    Object.values(pollTimers.current).forEach(clearTimeout);
    pollTimers.current = {};
    void fetchJobs(DEMO_TOKEN);
  };

  const fetchJobs = async (authToken: string) => {
    try {
      const res = await fetch("/api/jobs", {
        headers: { Authorization: `Bearer ${authToken}` },
      });
      if (res.ok) {
        const data = await res.json();
        setJobs(data.jobs || []);
        // Start polling for running/queued jobs
        data.jobs.forEach((job: any) => {
          if (job.status === "running" || job.status === "queued") {
            startPolling(job.job_id, authToken);
          }
        });
      }
    } catch (err) {
      console.error("Failed to load jobs", err);
    }
  };

  const startPolling = (jobId: string, authToken: string) => {
    if (pollTimers.current[jobId]) return;

    const poll = async () => {
      try {
        const res = await fetch(`/api/jobs/${jobId}`, {
          headers: { Authorization: `Bearer ${authToken}` },
        });
        if (res.ok) {
          const job = await res.json();
          setJobs((prev) =>
            prev.map((j) => (j.job_id === jobId ? job : j))
          );

          // Update active modal view if it matches the polled job
          if (selectedJobIdRef.current === jobId) {
            setActiveJob(job);
            void fetchReviewsForJob(jobId, authToken);
          }

          if (job.status === "completed" || job.status === "failed" || job.status === "cancelled") {
            stopPolling(jobId);
          } else {
            pollTimers.current[jobId] = setTimeout(poll, POLL_INTERVAL_MS);
          }
        } else {
          stopPolling(jobId);
        }
      } catch {
        stopPolling(jobId);
      }
    };

    pollTimers.current[jobId] = setTimeout(poll, POLL_INTERVAL_MS);
  };

  const stopPolling = (jobId: string) => {
    if (pollTimers.current[jobId]) {
      clearTimeout(pollTimers.current[jobId]);
      delete pollTimers.current[jobId];
    }
  };

  // Drag & Drop handlers
  const handleDrag = (e: React.DragEvent) => {
    e.preventDefault();
    e.stopPropagation();
    if (e.type === "dragenter" || e.type === "dragover") {
      setDragActive(true);
    } else if (e.type === "dragleave") {
      setDragActive(false);
    }
  };

  const handleDrop = (e: React.DragEvent) => {
    e.preventDefault();
    e.stopPropagation();
    setDragActive(false);
    if (e.dataTransfer.files && e.dataTransfer.files[0]) {
      const file = e.dataTransfer.files[0];
      if (validateFile(file)) setSelectedFile(file);
    }
  };

  const validateFile = (file: File) => {
    const allowed = [".mp4", ".mov", ".avi", ".webm"];
    const ext = file.name.slice(file.name.lastIndexOf(".")).toLowerCase();
    if (!allowed.includes(ext)) {
      alert("Unsupported file type. Please select MP4, MOV, AVI, or WEBM.");
      return false;
    }
    return true;
  };

  const handleFileChange = (e: React.ChangeEvent<HTMLInputElement>) => {
    if (e.target.files && e.target.files[0]) {
      const file = e.target.files[0];
      if (validateFile(file)) setSelectedFile(file);
    }
  };

  const handleUploadSubmit = async () => {
    if (!selectedFile || !token) return;
    setIsUploading(true);
    setUploadProgress(0);

    const formData = new FormData();
    formData.append("video", selectedFile);

    try {
      const res = await fetch("/api/jobs/upload", {
        method: "POST",
        headers: { Authorization: `Bearer ${token}` },
        body: formData,
      });

      const data = await res.json();
      if (!res.ok) {
        if (res.status === 409) {
          alert("You already have an active job. Please wait for it to complete.");
        } else {
          throw new Error(data.detail || "Upload failed");
        }
        return;
      }

      setSelectedFile(null);
      setJobs((prev) => [data, ...prev]);
      startPolling(data.job_id, token);
    } catch (err: any) {
      alert(err.message || "Upload failed");
    } finally {
      setIsUploading(false);
      setUploadProgress(null);
    }
  };

  const handleDeleteJob = async (jobId: string) => {
    if (!token) return;
    if (!confirm("Are you sure you want to delete this job and all associated artifacts?")) return;
    
    try {
      const res = await fetch(`/api/jobs/${jobId}`, {
        method: "DELETE",
        headers: { Authorization: `Bearer ${token}` },
      });
      if (res.ok) {
        stopPolling(jobId);
        setJobs((prev) => prev.filter((j) => j.job_id !== jobId));
        if (selectedJobId === jobId) handleBackToDashboard();
      }
    } catch (err) {
      console.error("Delete failed", err);
    }
  };

  const handleDownloadDataset = async (jobId: string, filename: string, key = "dataset_robot_zip") => {
    if (!token) return;
    try {
      let res = await fetch(`/api/jobs/${jobId}/downloads/${key}`, {
        headers: { Authorization: `Bearer ${token}` },
      });
      
      // Fallback to legacy endpoint if new fine-grained downloader is not present
      if (res.status === 404) {
        console.warn("Fine-grained downloader not found, falling back to legacy single-zip download");
        res = await fetch(`/api/jobs/${jobId}/download`, {
          headers: { Authorization: `Bearer ${token}` },
        });
      }

      if (!res.ok) {
        const data = await res.json().catch(() => null);
        throw new Error((data && data.detail) || "Download failed");
      }

      const blob = await res.blob();
      const url = URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = url;
      const suffix = key === "dataset_skeleton_zip" ? "_skeleton" : "_robot";
      a.download = `${filename.replace(/\.[^.]+$/, "")}${suffix}.zip`;
      document.body.appendChild(a);
      a.click();
      a.remove();
      URL.revokeObjectURL(url);
    } catch (err: any) {
      alert(err.message || "Download failed");
    }
  };

  // Open Details View
  const handleOpenDetails = async (jobId: string) => {
    if (!token) return;
    window.history.pushState(null, "", `/jobs/${jobId}`);
    setSelectedJobId(jobId);
    setPlaybackTime(0);
    setPlaybackDuration(0);
    setScrubTriggerTime(null);
    setVerdicts({ pose: null, retarget: null });
    setReviewsData(null);
    setModalRightTab("reports");

    const job = jobs.find((j) => j.job_id === jobId);
    setActiveJob(job || null);

    // Fetch dual reviews snapshot
    const reviewData = await fetchReviewsForJob(jobId, token);
    if (!reviewData) {
      console.warn("Reviews API not available, will fall back to legacy rendering");
    }
  };

  const handleBackToDashboard = () => {
    window.history.pushState(null, "", "/");
    setSelectedJobId(null);
    setActiveJob(null);
    setReviewsData(null);
    setVerdicts({ pose: null, retarget: null });
    setModalRightTab("reports");
  };

  const handleVerdictChange = (stage: "pose" | "retarget", val: string | null) => {
    setVerdicts((prev) => ({ ...prev, [stage]: val }));
  };

  const handleTimeUpdate = (time: number, dur: number) => {
    setPlaybackTime(time);
    setPlaybackDuration(dur);
    setScrubTriggerTime(null); // Reset trigger once updated
  };

  const handleScrub = (time: number) => {
    setScrubTriggerTime(time);
    setPlaybackTime(time);
  };

  // Render Check: show salvage banner if pose is approved and retarget failed
  const showSalvageBanner =
    (verdicts.pose === "approved" || verdicts.pose === "usable_skeleton_only") &&
    (verdicts.retarget === "rejected" || verdicts.retarget === "needs_review");

  const hasRobot = activeJob?.result && (activeJob.result.downsampled_trajectory || activeJob.result.robot_simulation_video);

  if (isAuthChecking) {
    return (
      <div className="flex min-h-screen items-center justify-center bg-bg text-slate-100">
        <div className="flex flex-col items-center gap-3">
          <Loader2 className="h-8 w-8 animate-spin text-accent" />
          <span className="text-xs font-semibold tracking-wider text-slate-400 uppercase">
            Loading Session...
          </span>
        </div>
      </div>
    );
  }

  return (
    <div className="flex min-h-screen flex-col bg-bg text-slate-100">
      {/* Header */}
      <header className="flex items-center justify-between border-b border-slate-900 bg-slate-950/40 p-4 px-6 backdrop-blur-md">
        <div className="flex items-center gap-2">
          <div className="flex h-7 w-7 items-center justify-center rounded-lg bg-accent-dim">
            <svg
              viewBox="0 0 32 32"
              fill="none"
              xmlns="http://www.w3.org/2000/svg"
              className="h-5 w-5 stroke-accent"
              strokeWidth="2"
            >
              <path d="M10 16l4-5.33h5.33L23.33 16 19 21.33H13.67L10 16z" fill="none" />
              <circle cx="16" cy="16" r="2" className="fill-accent" />
            </svg>
          </div>
          <span className="font-bold tracking-tight text-slate-100 text-lg">RoboData</span>
        </div>

        <div className="flex items-center gap-4">
          <div className="flex flex-col items-end">
            <span className="text-[10px] font-bold text-accent uppercase tracking-wider">
              {role}
            </span>
            <span className="font-mono text-[10px] text-slate-500">
              ID: {sessionId ? `${sessionId.slice(0, 8)}...` : "Global"}
            </span>
          </div>
          <button
            onClick={handleLogout}
            className="flex h-8 items-center gap-1.5 rounded-lg border border-slate-800 bg-slate-900/60 px-3 text-xs font-semibold text-slate-400 hover:text-slate-200 transition-colors"
          >
            <LogOut className="h-3.5 w-3.5" /> Logout
          </button>
        </div>
      </header>

      {/* Main content area */}
      {selectedJobId && activeJob ? (
        /* Full-Screen Details View (Master-Sidebar) */
        <div className="flex-1 flex flex-col max-w-7xl mx-auto w-full p-6 gap-4 min-h-0 animate-in fade-in duration-200">
          {/* Back Navigation Bar */}
          <div className="flex items-center justify-between border-b border-slate-900 pb-3">
            <div className="flex items-center gap-3">
              <button
                onClick={handleBackToDashboard}
                className="flex items-center gap-1.5 rounded-lg border border-slate-800 bg-slate-900/60 px-3 py-1.5 text-xs font-semibold text-slate-400 hover:text-slate-200 transition-colors"
              >
                ← Back to Dashboard
              </button>
              <span className="text-sm font-bold text-slate-100 truncate">
                Job Details: {activeJob.filename}
              </span>
              <span className={`rounded-full px-2 py-0.5 text-[9px] font-bold uppercase tracking-wider ${
                activeJob.status === "completed" ? "bg-emerald-500/10 text-emerald-500" : activeJob.status === "failed" ? "bg-rose-500/10 text-rose-500" : "bg-accent-dim text-accent"
              }`}>
                {activeJob.status}
              </span>
            </div>
          </div>

          {/* Body Columns */}
          <div className="grid flex-1 grid-cols-1 lg:grid-cols-[2.7fr_1.3fr] gap-6 overflow-y-auto min-h-0">
            {/* Left Column: Media & Charts */}
            <div className="flex flex-col gap-4 min-w-0">
              <VideoInspector
                jobId={activeJob.job_id}
                token={token}
                hasRobot={!!hasRobot}
                onTimeUpdate={handleTimeUpdate}
                scrubTime={scrubTriggerTime}
              />
              
              {activeJob.status === "completed" && (
                <TrajectoryChart
                  trajectory={activeJob.result?.downsampled_trajectory || []}
                  currentTime={playbackTime}
                  duration={playbackDuration}
                  onScrub={handleScrub}
                />
              )}
            </div>

            {/* Right Column: Meta & Reviews / Chat */}
            <div className="flex flex-col gap-4 overflow-y-auto pr-1">
              {/* Tab Switcher */}
              <div className="flex gap-1 border-b border-slate-900 pb-2">
                <button
                  onClick={() => setModalRightTab("reports")}
                  className={`flex items-center gap-1.5 px-3 py-1.5 text-xs font-semibold rounded-lg transition-all ${
                    modalRightTab === "reports"
                      ? "bg-slate-900 text-accent shadow-sm"
                      : "text-slate-400 hover:text-slate-200"
                  }`}
                >
                  <FileText className="h-3.5 w-3.5" /> Automated Reports
                </button>
                <button
                  onClick={() => setModalRightTab("chat")}
                  className={`flex items-center gap-1.5 px-3 py-1.5 text-xs font-semibold rounded-lg transition-all ${
                    modalRightTab === "chat"
                      ? "bg-slate-900 text-accent shadow-sm"
                      : "text-slate-400 hover:text-slate-200"
                  }`}
                >
                  <MessageSquare className="h-3.5 w-3.5" /> Interactive AI Agent
                </button>
                <button
                  onClick={() => setModalRightTab("orchestration")}
                  className={`flex items-center gap-1.5 px-3 py-1.5 text-xs font-semibold rounded-lg transition-all ${
                    modalRightTab === "orchestration"
                      ? "bg-slate-900 text-accent shadow-sm"
                      : "text-slate-400 hover:text-slate-200"
                  }`}
                >
                  <Sparkles className="h-3.5 w-3.5" /> Adaptive Orchestrator
                </button>
              </div>

              {modalRightTab === "reports" ? (
                <>
                  {/* Pipeline Stats */}
                  <div className="rounded-xl border border-slate-800 bg-slate-900/20 p-4">
                    <h4 className="text-xs font-semibold tracking-wider text-slate-400 uppercase mb-2">
                      Submission Stats
                    </h4>
                    <table className="w-full text-xs text-left border-collapse">
                      <tbody>
                        <tr className="border-b border-slate-900/50"><td className="py-2 text-slate-500">Job ID</td><td className="py-2 text-right font-mono text-slate-300 select-all">{activeJob.job_id}</td></tr>
                        <tr className="border-b border-slate-900/50"><td className="py-2 text-slate-500">Filename</td><td className="py-2 text-right text-slate-300">{activeJob.filename}</td></tr>
                        <tr className="border-b border-slate-900/50"><td className="py-2 text-slate-500">Timeline</td><td className="py-2 text-right text-slate-300">Started: {new Date(activeJob.created_at).toLocaleTimeString()}</td></tr>
                      </tbody>
                    </table>
                  </div>

                  {/* Static checks */}
                  <div className="rounded-xl border border-slate-800 bg-slate-900/20 p-4">
                    <h4 className="text-xs font-semibold tracking-wider text-slate-400 uppercase mb-2.5">
                      Static Checks Checklist
                    </h4>
                    <ul className="flex flex-col gap-2 text-xs">
                      {activeJob.status === "completed" && activeJob.result?.static_checks?.checks ? (
                        activeJob.result.static_checks.checks.map((c: any, idx: number) => (
                          <li key={`check-${idx}`} className={`flex items-start gap-2.5 rounded-lg bg-slate-950/60 p-2 border-l-2 ${c.passed ? "border-emerald-500" : "border-rose-500"}`}>
                            <span className="text-sm leading-none mt-0.5">{c.passed ? "✅" : "❌"}</span>
                            <div className="flex flex-col">
                              <span className="font-semibold text-slate-200">{c.name}</span>
                              <span className="text-[10px] text-slate-500 mt-0.5">{c.details}</span>
                            </div>
                          </li>
                        ))
                      ) : (
                        <li className="text-slate-500 italic">Checks will populate once completed.</li>
                      )}
                    </ul>
                  </div>

                  {/* Stream review 1: Pose */}
                  <ReviewPanel
                    jobId={activeJob.job_id}
                    token={token}
                    stage="pose"
                    reviewInfo={reviewsData?.pose}
                    legacyMarkdown=""
                    jobStatus={activeJob.status}
                    onVerdictChange={handleVerdictChange}
                  />

                  {/* Stream review 2: Retarget */}
                  <ReviewPanel
                    jobId={activeJob.job_id}
                    token={token}
                    stage="retarget"
                    reviewInfo={reviewsData?.retarget}
                    legacyMarkdown={activeJob.result?.ai_review || ""}
                    jobStatus={activeJob.status}
                    onVerdictChange={handleVerdictChange}
                  />
                </>
              ) : modalRightTab === "chat" ? (
                <AssistantChat jobId={activeJob.job_id} token={token} />
              ) : (
                <OrchestratorPanel jobId={activeJob.job_id} token={token} />
              )}
            </div>
          </div>

          {/* Action Bar Footer */}
          <div className="flex flex-col sm:flex-row items-center justify-between border-t border-slate-900 bg-slate-950/10 py-4 gap-3 mt-auto">
            {showSalvageBanner && (
              <div className="flex items-center gap-2 rounded-lg border border-sky-500/20 bg-sky-500/10 p-2.5 px-3 max-w-lg">
                <span className="float-icon text-lg leading-none">💡</span>
                <span className="text-[11px] text-sky-400 leading-normal font-medium">
                  Skeleton salvage path available: Robot retargeting review rejected, but you can still download the pose-stage dataset.
                </span>
              </div>
            )}
            
            <div className="flex gap-2 ml-auto">
              {activeJob.status === "completed" && (
                <>
                  <button
                    onClick={() => handleDownloadDataset(activeJob.job_id, activeJob.filename, "dataset_skeleton_zip")}
                    className="rounded-lg border border-slate-800 bg-slate-900/60 px-4 py-2 text-xs font-semibold text-slate-300 hover:text-slate-100 hover:bg-slate-850 hover:border-slate-700 transition-colors"
                  >
                    ⬇ Download Skeleton Dataset
                  </button>
                  
                  {verdicts.retarget !== "rejected" && (
                    <button
                      onClick={() => handleDownloadDataset(activeJob.job_id, activeJob.filename, "dataset_robot_zip")}
                      className="rounded-lg bg-accent px-4 py-2 text-xs font-bold text-slate-950 hover:bg-accent-hover transition-all"
                    >
                      ⬇ Download Robot Joint Dataset
                    </button>
                  )}
                </>
              )}
            </div>
          </div>
        </div>
      ) : (
        /* Dashboard View (Upload + List) */
        <main className="grid flex-1 grid-cols-1 lg:grid-cols-[1.1fr_1.5fr] gap-6 p-6 overflow-hidden max-w-7xl mx-auto w-full">
          {/* Left Side: Upload Panel */}
          <section className="flex flex-col gap-4 rounded-2xl border border-slate-900 bg-slate-950/20 p-6 shadow-xl backdrop-blur-xl">
            <h2 className="text-sm font-bold tracking-wider text-slate-400 uppercase">
              Upload Demonstration
            </h2>

            <div
              onDragEnter={handleDrag}
              onDragOver={handleDrag}
              onDragLeave={handleDrag}
              onDrop={handleDrop}
              onClick={() => fileInputRef.current?.click()}
              className={`flex flex-col items-center justify-center rounded-xl border border-dashed p-8 text-center cursor-pointer transition-all duration-300 ${
                dragActive
                  ? "border-accent bg-accent-dim/10"
                  : "border-slate-800 bg-slate-950/50 hover:bg-slate-950/80 hover:border-slate-700"
              }`}
            >
              <input
                ref={fileInputRef}
                type="file"
                accept="video/mp4,video/mov,video/avi,video/webm,.mp4,.mov,.avi,.webm"
                onChange={handleFileChange}
                hidden
              />
              <UploadCloud className={`h-10 w-10 text-slate-500 mb-3 transition-transform duration-300 ${dragActive ? "scale-110 text-accent" : ""}`} />
              <p className="text-xs font-semibold text-slate-300">
                Drag &amp; drop a video here, or click to browse
              </p>
              <p className="text-[10px] text-slate-500 mt-1">MP4, MOV, AVI, WEBM (Max 100MB / 30s)</p>
            </div>

            {selectedFile && (
              <div className="flex flex-col gap-3 rounded-lg border border-slate-800 bg-slate-950 p-4">
                <div className="flex items-center justify-between">
                  <span className="truncate text-xs font-medium text-slate-300">
                    {selectedFile.name}
                  </span>
                  <button
                    disabled={isUploading}
                    onClick={() => setSelectedFile(null)}
                    className="text-slate-500 hover:text-slate-300"
                  >
                    <X className="h-4 w-4" />
                  </button>
                </div>

                {isUploading ? (
                  <div className="flex items-center gap-2 text-xs text-accent">
                    <Loader2 className="h-3.5 w-3.5 animate-spin" />
                    <span>Processing video file...</span>
                  </div>
                ) : (
                  <button
                    onClick={handleUploadSubmit}
                    className="w-full rounded-lg bg-accent py-2 text-xs font-bold text-slate-950 hover:bg-accent-hover transition-colors shadow-md"
                  >
                    Upload &amp; Process
                  </button>
                )}
              </div>
            )}
          </section>

          {/* Right Side: Jobs List */}
          <section className="flex flex-col gap-4 rounded-2xl border border-slate-900 bg-slate-950/20 p-6 shadow-xl backdrop-blur-xl overflow-y-auto">
            <h2 className="text-sm font-bold tracking-wider text-slate-400 uppercase">
              Regeneration Pipeline Jobs
            </h2>

            <div className="flex flex-col gap-3">
              {jobs.length === 0 ? (
                <div className="flex h-48 flex-col items-center justify-center rounded-xl border border-slate-900 bg-slate-950/50 p-8 text-center text-slate-500">
                  <Sparkles className="h-8 w-8 text-slate-600 mb-2" />
                  <p className="text-xs font-medium">No job submissions found.</p>
                  <p className="text-[10px] text-slate-600 mt-0.5">Upload a video to start the pose estimation &amp; retargeting pipeline.</p>
                </div>
              ) : (
                jobs.map((job) => (
                  <JobCard
                    key={job.job_id}
                    job={job}
                    onViewDetails={handleOpenDetails}
                    onDownload={(id, fn) => handleDownloadDataset(id, fn, "dataset_robot_zip")}
                    onDelete={handleDeleteJob}
                  />
                ))
              )}
            </div>
          </section>
        </main>
      )}
    </div>
  );
}
