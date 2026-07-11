"use client";

import React, { useState, useEffect, useRef } from "react";
import { AlertCircle, CheckCircle, HelpCircle, XCircle, RefreshCw } from "lucide-react";

interface ReviewPanelProps {
  jobId: string;
  token: string;
  stage: "pose" | "retarget";
  reviewInfo: any | null; // From /api/jobs/{id}/reviews if available
  legacyMarkdown: string; // Fallback
  jobStatus: string;
  onVerdictChange: (stage: "pose" | "retarget", verdict: string | null) => void;
}

export default function ReviewPanel({
  jobId,
  token,
  stage,
  reviewInfo,
  legacyMarkdown,
  jobStatus,
  onVerdictChange,
}: ReviewPanelProps) {
  const [status, setStatus] = useState<string>("pending");
  const [verdict, setVerdict] = useState<string | null>(null);
  const [markdown, setMarkdown] = useState<string>("");
  const [error, setError] = useState<string | null>(null);
  const bodyRef = useRef<HTMLDivElement>(null);
  const eventSourceRef = useRef<EventSource | null>(null);

  const closeStream = () => {
    if (eventSourceRef.current) {
      eventSourceRef.current.close();
      eventSourceRef.current = null;
    }
  };

  useEffect(() => {
    // Reset state on jobId change
    setStatus("pending");
    setVerdict(null);
    setMarkdown("");
    setError(null);
    closeStream();

    if (reviewInfo) {
      // API is available
      setStatus(reviewInfo.status);
      setVerdict(reviewInfo.verdict);
      onVerdictChange(stage, reviewInfo.verdict);

      if (reviewInfo.status === "completed") {
        fetchPersistedReview();
      } else if (reviewInfo.status === "running" || reviewInfo.status === "pending") {
        startSseStream();
      } else if (reviewInfo.status === "failed") {
        setError(reviewInfo.error || "Review process failed.");
      }
    } else {
      // Fallback mode: backend agent hasn't merged dual reviews yet
      if (jobStatus === "completed") {
        setStatus("completed");
        if (stage === "pose") {
          setVerdict("approved");
          onVerdictChange("pose", "approved");
          setMarkdown("### Pose extraction completed\n(Standard local review fallback mode)");
        } else {
          // If retarget, parse from legacyMarkdown
          const parsedVerdict = legacyMarkdown.includes("🟢 Approved")
            ? "approved"
            : legacyMarkdown.includes("🔴 Rejected")
            ? "rejected"
            : "needs_review";
          setVerdict(parsedVerdict);
          onVerdictChange("retarget", parsedVerdict);
          setMarkdown(legacyMarkdown);
        }
      } else if (jobStatus === "failed") {
        setStatus("failed");
        setVerdict("rejected");
        onVerdictChange(stage, "rejected");
        setError("Job pipeline failed. Review cancelled.");
      } else {
        setStatus("pending");
        setMarkdown("Review report will generate once job is completed.");
      }
    }

    return () => closeStream();
  }, [jobId, reviewInfo, legacyMarkdown, jobStatus]);

  const fetchPersistedReview = async () => {
    try {
      const artifactKey = `${stage}_review_md`;
      const res = await fetch(`/api/jobs/${jobId}/downloads/${artifactKey}`, {
        headers: { Authorization: `Bearer ${token}` },
      });
      if (res.ok) {
        setMarkdown(await res.text());
      } else {
        setMarkdown(reviewInfo?.summary || "No review content found.");
      }
    } catch {
      setMarkdown(reviewInfo?.summary || "No review content found.");
    }
  };

  const startSseStream = () => {
    closeStream();
    const source = new EventSource(`/api/jobs/${jobId}/reviews/${stage}/stream?token=${token}`);
    eventSourceRef.current = source;

    let textBuffer = "";

    source.addEventListener("status", (e) => {
      try {
        const payload = JSON.parse(e.data);
        setStatus(payload.status || "running");
      } catch {
        setStatus("running");
      }
    });

    source.addEventListener("token", (e) => {
      try {
        const payload = JSON.parse(e.data);
        textBuffer += payload.text || "";
      } catch {
        textBuffer += e.data;
      }
      setMarkdown(textBuffer);

      // Auto scroll to bottom during typing animation
      if (bodyRef.current) {
        bodyRef.current.scrollTop = bodyRef.current.scrollHeight;
      }
    });

    source.addEventListener("result", (e) => {
      try {
        const res = JSON.parse(e.data);
        setVerdict(res.verdict);
        onVerdictChange(stage, res.verdict);
      } catch {}
    });

    source.addEventListener("error", (e) => {
      console.error(`SSE stream error on ${stage} review`, e);
      setStatus("failed");
      setError("Stream connection interrupted.");
      closeStream();
    });

    source.addEventListener("done", (e) => {
      try {
        const payload = JSON.parse(e.data);
        setStatus(payload.status || "completed");
      } catch {
        setStatus("completed");
      }
      closeStream();
      void fetchPersistedReview();
    });
  };

  const getStatusBadgeClass = (s: string) => {
    const classes = {
      pending: "bg-amber-500/10 text-amber-500 border border-amber-500/20",
      running: "bg-accent-dim text-accent border border-accent/20",
      completed: "bg-emerald-500/10 text-emerald-500 border border-emerald-500/20",
      failed: "bg-rose-500/10 text-rose-500 border border-rose-500/20",
    };
    return classes[s as keyof typeof classes] || "bg-slate-800 text-slate-400";
  };

  const getVerdictBadge = (v: string | null) => {
    if (!v) return null;
    const items = {
      approved: {
        text: "Approved",
        icon: <CheckCircle className="h-3 w-3" />,
        cls: "bg-emerald-500/15 text-emerald-400 border border-emerald-500/30",
      },
      usable_skeleton_only: {
        text: "Skeleton Usable Only",
        icon: <HelpCircle className="h-3 w-3" />,
        cls: "bg-sky-500/15 text-sky-400 border border-sky-500/30",
      },
      needs_review: {
        text: "Needs Review",
        icon: <AlertCircle className="h-3 w-3" />,
        cls: "bg-amber-500/15 text-amber-400 border border-amber-500/30",
      },
      rejected: {
        text: "Rejected",
        icon: <XCircle className="h-3 w-3" />,
        cls: "bg-rose-500/15 text-rose-400 border border-rose-500/30",
      },
    };
    const details = items[v as keyof typeof items];
    if (!details) return null;

    return (
      <span className={`flex items-center gap-1 rounded-full px-2 py-0.5 text-[10px] font-bold uppercase tracking-wider ${details.cls}`}>
        {details.icon}
        {details.text}
      </span>
    );
  };

  const parseMarkdownHtml = (md: string) => {
    if (!md) return "";
    const lines = md.split("\n");
    let html = "";
    let inList = false;
    let inTable = false;
    let tableHeaderParsed = false;

    for (let i = 0; i < lines.length; i++) {
      let line = lines[i].trim();

      if (line.startsWith("|")) {
        if (inList) { inList = false; html += "</ul>"; }
        if (!inTable) { inTable = true; html += '<table class="w-full border-collapse border border-slate-800 my-2 text-xs">'; tableHeaderParsed = false; }
        const cells = line.split("|").slice(1, -1).map((c) => c.trim());
        if (cells.every((c) => /^:?-+:?$/.test(c))) continue;
        html += '<tr class="border-b border-slate-800">';
        cells.forEach((cell) => {
          if (!tableHeaderParsed) {
            html += `<th class="border border-slate-800 bg-slate-900 px-2 py-1 text-left font-semibold text-slate-300">${cell}</th>`;
          } else {
            html += `<td class="border border-slate-800 px-2 py-1">${cell}</td>`;
          }
        });
        html += "</tr>";
        tableHeaderParsed = true;
        continue;
      } else if (inTable) {
        html += "</table>";
        inTable = false;
      }

      if (line.startsWith("#")) {
        if (inList) { inList = false; html += "</ul>"; }
        const level = line.match(/^#+/)?.[0].length || 1;
        const text = line.replace(/^#+\s*/, "");
        const sizes = ["text-lg font-bold border-b border-slate-800 pb-1 mt-3 mb-2 text-slate-100", "text-sm font-bold mt-2.5 mb-1.5 text-slate-200", "text-xs font-semibold text-slate-300"];
        html += `<h${level} class="${sizes[level - 1] || "text-xs"}">${text}</h${level}>`;
        continue;
      }

      if (line === "---") {
        if (inList) { inList = false; html += "</ul>"; }
        html += '<hr class="border-slate-800 my-3">';
        continue;
      }

      if (line.startsWith("- ")) {
        if (!inList) { inList = true; html += '<ul class="list-disc pl-4 space-y-1 my-2">'; }
        html += `<li>${line.substring(2)}</li>`;
        continue;
      } else if (inList) {
        inList = false;
        html += "</ul>";
      }

      if (line !== "") {
        html += `<p class="mb-2 last:mb-0 text-slate-400">${line}</p>`;
      }
    }

    if (inList) html += "</ul>";
    if (inTable) html += "</table>";

    // Format bold tags
    html = html.replace(/\*\*(.*?)\*\*/g, '<strong class="font-semibold text-slate-200">$1</strong>');
    return html;
  };

  return (
    <div className="flex flex-col gap-2 rounded-xl border border-slate-800 bg-slate-900/40 p-4">
      {/* Header */}
      <div className="info-block-header flex items-center justify-between">
        <h4 className="flex items-center gap-1.5 text-xs font-semibold tracking-wider text-slate-300 uppercase">
          Stage {stage === "pose" ? "1" : "2"}: {stage === "pose" ? "Pose Review" : "Retarget Review"}
          {status === "running" && <span className="pulse-live h-1.5 w-1.5 rounded-full bg-accent" />}
        </h4>
        <div className="flex items-center gap-1.5">
          <span className={`rounded px-1.5 py-0.5 text-[9px] font-bold uppercase tracking-wide ${getStatusBadgeClass(status)}`}>
            {status}
          </span>
          {getVerdictBadge(verdict)}
        </div>
      </div>

      {/* Body Report */}
      <div
        ref={bodyRef}
        className="max-h-60 overflow-y-auto rounded-lg border border-slate-800 bg-slate-950/70 p-3.5 text-xs text-slate-400 leading-relaxed font-sans scroll-smooth"
        dangerouslySetInnerHTML={{ __html: parseMarkdownHtml(markdown) }}
      />

      {error && (
        <div className="flex items-center gap-2 rounded-lg border border-rose-500/20 bg-rose-500/10 p-2.5 text-xs text-rose-400">
          <AlertCircle className="h-4 w-4 shrink-0" />
          <span>{error}</span>
        </div>
      )}
    </div>
  );
}
