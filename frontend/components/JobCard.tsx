"use client";

import React from "react";
import { Eye, Download, Trash2, Calendar, FileVideo } from "lucide-react";

interface JobCardProps {
  job: {
    job_id: string;
    filename: string;
    status: string;
    progress: number;
    current_stage?: string;
    message?: string;
    created_at: string;
  };
  onViewDetails: (jobId: string) => void;
  onDownload: (jobId: string, filename: string) => void;
  onDelete: (jobId: string) => void;
}

export default function JobCard({
  job,
  onViewDetails,
  onDownload,
  onDelete,
}: JobCardProps) {
  const pct = Math.round((job.progress || 0) * 100);

  const getStatusLabel = (s: string) => {
    const map = {
      queued: "Queued",
      running: "Running",
      completed: "Complete",
      failed: "Failed",
      cancelled: "Cancelled",
    };
    return map[s as keyof typeof map] || s;
  };

  const getStatusBadgeClass = (s: string) => {
    const map = {
      queued: "bg-amber-500/10 text-amber-500",
      running: "bg-accent-dim text-accent",
      completed: "bg-emerald-500/10 text-emerald-500",
      failed: "bg-rose-500/10 text-rose-500",
      cancelled: "bg-slate-800 text-slate-400",
    };
    return map[s as keyof typeof map] || "bg-slate-800 text-slate-400";
  };

  const getProgressFillClass = (s: string) => {
    if (s === "failed" || s === "cancelled") return "bg-rose-500";
    if (s === "completed") return "bg-emerald-500";
    return "bg-accent";
  };

  const formatTime = (isoString: string) => {
    if (!isoString) return "";
    const then = new Date(isoString);
    const now = new Date();
    const diffMs = now.getTime() - then.getTime();
    const diffSec = Math.floor(diffMs / 1000);

    if (diffSec < 10) return "just now";
    if (diffSec < 60) return `${diffSec}s ago`;
    const diffMin = Math.floor(diffSec / 60);
    if (diffMin < 60) return `${diffMin}m ago`;
    const diffHr = Math.floor(diffMin / 60);
    if (diffHr < 24) return `${diffHr}h ago`;
    return then.toLocaleDateString(undefined, {
      month: "short",
      day: "numeric",
      hour: "numeric",
      minute: "2-digit",
    });
  };

  return (
    <div className="flex flex-col gap-3 rounded-xl border border-slate-800 bg-slate-900/20 p-5 hover:bg-slate-900/40 hover:border-slate-800/80 transition-all duration-200">
      <div className="flex items-center justify-between gap-3">
        <div className="flex items-center gap-2 min-w-0">
          <FileVideo className="h-4 w-4 shrink-0 text-slate-500" />
          <span className="truncate text-sm font-semibold text-slate-100">
            {job.filename}
          </span>
        </div>
        <span className={`rounded-full px-2 py-0.5 text-[10px] font-bold uppercase tracking-wider ${getStatusBadgeClass(job.status)}`}>
          {getStatusLabel(job.status)}
        </span>
      </div>

      {/* Stage Status / Errors */}
      {job.status === "running" && job.current_stage && (
        <span className="text-xs text-slate-400">
          Current Stage: <span className="font-semibold text-accent">{job.current_stage}</span>
        </span>
      )}
      {job.status === "failed" && job.message && (
        <span className="text-xs text-rose-400 font-medium">
          Error: {job.message}
        </span>
      )}

      {/* Progress slider bar */}
      <div className="flex items-center gap-3">
        <div className="h-2 w-full rounded-full bg-slate-950 overflow-hidden">
          <div
            className={`h-full rounded-full transition-all duration-300 ${getProgressFillClass(job.status)}`}
            style={{ width: `${pct}%` }}
          />
        </div>
        <span className="w-8 text-right font-mono text-xs font-semibold text-slate-400">
          {pct}%
        </span>
      </div>

      <div className="mt-2 flex items-center justify-between border-t border-slate-900 pt-3">
        <span className="flex items-center gap-1 text-[10px] text-slate-500">
          <Calendar className="h-3 w-3" />
          Started {formatTime(job.created_at)}
        </span>

        {/* Action button rows */}
        <div className="flex gap-2">
          <button
            onClick={() => onDelete(job.job_id)}
            className="flex h-7 items-center justify-center rounded-md border border-slate-800 bg-slate-950 px-2.5 text-xs font-medium text-slate-500 hover:border-rose-500/30 hover:bg-rose-500/10 hover:text-rose-400 transition-all"
            title="Delete job"
          >
            <Trash2 className="h-3.5 w-3.5" />
          </button>
          
          {(job.status === "completed" || job.status === "failed") && (
            <button
              onClick={() => onViewDetails(job.job_id)}
              className="flex h-7 items-center gap-1 rounded-md border border-slate-800 bg-slate-950 px-2.5 text-xs font-semibold text-slate-300 hover:bg-slate-900 hover:border-slate-700 transition-all"
            >
              <Eye className="h-3.5 w-3.5" /> View Details
            </button>
          )}

          {job.status === "completed" && (
            <button
              onClick={() => onDownload(job.job_id, job.filename)}
              className="flex h-7 items-center gap-1 rounded-md bg-accent px-2.5 text-xs font-semibold text-slate-950 hover:bg-accent-hover active:scale-[0.98] transition-all"
            >
              <Download className="h-3.5 w-3.5" /> Download
            </button>
          )}
        </div>
      </div>
    </div>
  );
}
