"use client";

import React, { useEffect, useRef, useState } from "react";
import {
  AlertCircle,
  Bot,
  Check,
  ChevronRight,
  Clock,
  GitBranch,
  Loader2,
  Play,
  RotateCcw,
  Save,
  Wrench,
  XCircle,
} from "lucide-react";

import {
  OrchestrationDonePayload,
  OrchestrationProgressPayload,
  OrchestrationResultPayload,
  OrchestrationSnapshotResponse,
  OrchestrationStatusPayload,
  OrchestrationTokenPayload,
  OrchestrationTracePayload,
} from "../generated/orchestration";

type OrchestrationUiSnapshot = Omit<
  OrchestrationSnapshotResponse,
  "provider" | "sandbox"
> & {
  provider?: OrchestrationSnapshotResponse["provider"];
  sandbox?: OrchestrationSnapshotResponse["sandbox"];
};

type TraceRole = "system" | "ai" | "tool" | "decision";

interface TraceEntry {
  role: TraceRole;
  phase: string;
  title: string;
  content: string;
  tool_name?: string | null;
  metadata?: Record<string, unknown>;
  heartbeat?: boolean;
}

interface MappingCheckpoint {
  checkpoint_id: string;
  author: string;
  summary?: string | null;
  created_at: string;
  parent_checkpoint_id?: string | null;
  mapping_profile: Record<string, unknown>;
}

interface RerunResponse {
  rerun_id: string;
  version: number;
  job_id: string;
  session_id: string;
  source_checkpoint_id: string;
  status: string;
  mapping_profile: Record<string, unknown>;
  artifact_manifest: Record<string, unknown> | null;
  summary: string | null;
  error: string | null;
  created_at: string;
  updated_at: string;
  completed_at: string | null;
  metadata: Record<string, unknown>;
}

interface RerunListResponse {
  reruns: RerunResponse[];
}

interface MappingSessionSession {
  session_id: string;
  current_checkpoint_id?: string | null;
  status: string;
  title?: string | null;
  active_rerun_id?: string | null;
  latest_rerun_id?: string | null;
  latest_completed_rerun_id?: string | null;
}

interface MappingSession {
  session: MappingSessionSession;
  checkpoints: MappingCheckpoint[];
  reruns?: RerunResponse[];
}

interface SessionListResponse {
  sessions: Array<{ session_id: string }>;
}

type EditorSource = "checkpoint" | "tuned";

interface OrchestratorPanelProps {
  jobId: string;
  token: string;
}

export default function OrchestratorPanel({
  jobId,
  token,
}: OrchestratorPanelProps) {
  const [snapshot, setSnapshot] = useState<OrchestrationUiSnapshot | null>(null);
  const [mappingSession, setMappingSession] = useState<MappingSession | null>(null);
  const [mappingJson, setMappingJson] = useState("");
  const [feedback, setFeedback] = useState<string | null>(null);
  const [snapshotError, setSnapshotError] = useState<string | null>(null);
  const [sessionError, setSessionError] = useState<string | null>(null);
  const [isLoadingSnapshot, setIsLoadingSnapshot] = useState(false);
  const [isLoadingSession, setIsLoadingSession] = useState(false);
  const [isRunning, setIsRunning] = useState(false);
  const [isApplying, setIsApplying] = useState(false);
  const [isRestoring, setIsRestoring] = useState(false);
  const [traceEntries, setTraceEntries] = useState<TraceEntry[]>([]);
  const [draftSummary, setDraftSummary] = useState("");
  const [editorSource, setEditorSource] = useState<EditorSource>("checkpoint");
  const [selectedCheckpointId, setSelectedCheckpointId] = useState<string | null>(
    null,
  );
  const [reruns, setReruns] = useState<RerunResponse[]>([]);
  const [isTriggeringRerun, setIsTriggeringRerun] = useState(false);
  const feedbackTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const eventSourceRef = useRef<EventSource | null>(null);
  const rerunPollerRef = useRef<ReturnType<typeof setInterval> | null>(null);

  useEffect(() => {
    setTraceEntries([]);
    setDraftSummary("");
    setEditorSource("checkpoint");
    closeStream();
    stopRerunPoller();
    void fetchSnapshot();
    void fetchLatestSession();
    return () => {
      if (feedbackTimer.current) {
        clearTimeout(feedbackTimer.current);
      }
      closeStream();
      stopRerunPoller();
    };
  }, [jobId]);

  useEffect(() => {
    const status = snapshot?.status;
    const shouldStream = status === "pending" || status === "running";

    if (shouldStream) {
      if (!eventSourceRef.current) {
        startSseStream();
      }
      setIsRunning(true);
      return;
    }

    if (eventSourceRef.current) {
      closeStream();
    }
    setIsRunning(false);
  }, [jobId, token, snapshot?.status]);

  const closeStream = () => {
    if (eventSourceRef.current) {
      eventSourceRef.current.close();
      eventSourceRef.current = null;
    }
  };

  const tunedProfileJson = snapshot?.tuned_mapping_profile
    ? JSON.stringify(snapshot.tuned_mapping_profile, null, 2)
    : "";
  const hasTunedProfile = Boolean(snapshot?.tuned_mapping_profile);

  const syncEditorToSource = (
    source: EditorSource,
    nextSession: MappingSession | null,
    nextSnapshot: OrchestrationUiSnapshot | null,
  ) => {
    if (source === "tuned") {
      const nextTunedProfile = nextSnapshot?.tuned_mapping_profile;
      if (nextTunedProfile) {
        setMappingJson(JSON.stringify(nextTunedProfile, null, 2));
        return;
      }
      setEditorSource("checkpoint");
    }

    const currentCheckpoint = nextSession?.checkpoints.find(
      (checkpoint) => checkpoint.checkpoint_id === nextSession.session.current_checkpoint_id,
    );
    if (currentCheckpoint) {
      setMappingJson(JSON.stringify(currentCheckpoint.mapping_profile, null, 2));
      return;
    }
    if (!nextSession?.checkpoints.length) {
      setMappingJson("");
    }
  };

  const appendTrace = (entry: TraceEntry) => {
    const normalized: TraceEntry = {
      ...entry,
      content: entry.content.trim(),
    };
    if (!normalized.content) {
      return;
    }
    setTraceEntries((previous) => {
      const last = previous[previous.length - 1];
      if (
        last &&
        last.role === normalized.role &&
        last.phase === normalized.phase &&
        last.title === normalized.title &&
        last.content === normalized.content
      ) {
        return previous;
      }
      return [...previous.slice(-29), normalized];
    });
  };

  const startSseStream = () => {
    if (eventSourceRef.current) {
      return;
    }
    const source = new EventSource(`/api/jobs/${jobId}/orchestration/stream?token=${token}`);
    eventSourceRef.current = source;

    source.addEventListener("status", (event) => {
      try {
        const payload = JSON.parse(event.data) as OrchestrationStatusPayload;
        setSnapshot((previous) => {
          if (!previous) {
            return {
              job_id: jobId,
              status: payload.status || "running",
              metadata: {},
            };
          }
          return {
            ...previous,
            status: payload.status || previous.status,
          };
        });
      } catch {
        setSnapshot((previous) =>
          previous ? { ...previous, status: "running" } : previous,
        );
      }
    });

    source.addEventListener("progress", (event) => {
      try {
        const payload = JSON.parse(event.data) as OrchestrationProgressPayload;
        if (payload.heartbeat) {
          appendTrace({
            role: "system",
            phase: payload.phase || "running",
            title: "Heartbeat",
            content: payload.message || "Orchestration is still running.",
            heartbeat: true,
          });
        }
        if (payload.phase) {
          setSnapshot((previous) => {
            if (!previous) {
              return {
                job_id: jobId,
                status: "running",
                metadata: { current_phase: payload.phase },
              };
            }
            return {
              ...previous,
              metadata: {
                ...(previous.metadata || {}),
                current_phase: payload.phase,
              },
            };
          });
        }
      } catch {
        appendTrace({
          role: "system",
          phase: "error",
          title: "Malformed progress event",
          content: "Received an orchestration progress event that could not be parsed.",
        });
      }
    });

    source.addEventListener("token", (event) => {
      try {
        const payload = JSON.parse(event.data) as OrchestrationTokenPayload;
        setDraftSummary((previous) => previous + String(payload.text || ""));
      } catch {
        setDraftSummary((previous) => previous + event.data);
      }
    });

    source.addEventListener("trace", (event) => {
      try {
        const payload = JSON.parse(event.data) as OrchestrationTracePayload;
        appendTrace({
          role: payload.role,
          phase: payload.phase,
          title: payload.title,
          content: payload.content,
          tool_name: payload.tool_name,
          metadata: payload.metadata,
        });
      } catch {
        appendTrace({
          role: "system",
          phase: "error",
          title: "Malformed trace event",
          content: "Received an orchestration trace event that could not be parsed.",
        });
      }
    });

    source.addEventListener("result", (event) => {
      try {
        const payload = JSON.parse(event.data) as OrchestrationResultPayload;
        setSnapshot((previous) => ({
          ...(previous || { job_id: jobId, status: "completed", metadata: {} }),
          status: "completed",
          decision: payload.decision,
          summary: payload.summary,
          capture_guidance: payload.capture_guidance,
          metadata: {
            ...((previous && previous.metadata) || {}),
            confidence: payload.confidence,
            risks: payload.risks || [],
            current_phase: "completed",
          },
        }));
        if (payload.summary) {
          setDraftSummary(String(payload.summary));
        }
      } catch {
        appendTrace({
          role: "system",
          phase: "error",
          title: "Malformed result event",
          content: "Received an orchestration result event that could not be parsed.",
        });
      }
    });

    source.addEventListener("error", () => {
      appendTrace({
        role: "system",
        phase: String(snapshot?.metadata?.current_phase || "running"),
        title: "Stream reconnecting",
        content: "Waiting for orchestration stream to continue...",
      });
    });

    source.addEventListener("done", (event) => {
      try {
        const payload = JSON.parse(event.data) as OrchestrationDonePayload;
        setSnapshot((previous) =>
          previous
            ? { ...previous, status: payload.status || previous.status }
            : previous,
        );
      } catch {
        // ignore malformed done events
      }
      setIsRunning(false);
      closeStream();
      void fetchSnapshot();
    });
  };

  const pushFeedback = (message: string) => {
    setFeedback(message);
    if (feedbackTimer.current) {
      clearTimeout(feedbackTimer.current);
    }
    feedbackTimer.current = setTimeout(() => setFeedback(null), 4000);
  };

  const authHeaders = {
    Authorization: `Bearer ${token}`,
  };

  const fetchSnapshot = async () => {
    setIsLoadingSnapshot(true);
    setSnapshotError(null);
    try {
      const res = await fetch(`/api/jobs/${jobId}/orchestration`, {
        headers: authHeaders,
      });
      if (res.ok) {
        const data = (await res.json()) as OrchestrationSnapshotResponse;
        setSnapshot(data);
        syncEditorToSource(editorSource, mappingSession, data);
        if (data.summary) {
          setDraftSummary(data.summary);
        }
      } else if (res.status === 404) {
        setSnapshot(null);
      } else {
        setSnapshotError("Orchestration snapshot unavailable.");
      }
    } catch {
      setSnapshotError("Orchestration snapshot unavailable.");
    } finally {
      setIsLoadingSnapshot(false);
    }
  };

  const stopRerunPoller = () => {
    if (rerunPollerRef.current) {
      clearInterval(rerunPollerRef.current);
      rerunPollerRef.current = null;
    }
  };

  const fetchLatestSession = async () => {
    setIsLoadingSession(true);
    setSessionError(null);
    try {
      const listRes = await fetch(`/api/jobs/${jobId}/mapping-sessions`, {
        headers: authHeaders,
      });
      if (!listRes.ok) {
        if (listRes.status === 404) {
          setMappingSession(null);
        } else {
          setSessionError("Mapping session unavailable.");
        }
        return;
      }

      const listData = (await listRes.json()) as SessionListResponse;
      const firstSession = listData.sessions?.[0];
      if (!firstSession) {
        setMappingSession(null);
        return;
      }

      const detailRes = await fetch(
        `/api/jobs/${jobId}/mapping-sessions/${firstSession.session_id}`,
        { headers: authHeaders },
      );
      if (!detailRes.ok) {
        setSessionError("Mapping session unavailable.");
        return;
      }

      const detail = (await detailRes.json()) as MappingSession;
      setMappingSession(detail);
      setReruns(detail.reruns ?? []);
      syncEditorToSource(editorSource, detail, snapshot);
    } catch {
      setSessionError("Mapping session unavailable.");
    } finally {
      setIsLoadingSession(false);
    }
  };

  useEffect(() => {
    const activeRerunId = mappingSession?.session?.active_rerun_id;
    if (!activeRerunId) {
      stopRerunPoller();
      return;
    }

    if (rerunPollerRef.current) {
      return;
    }

    rerunPollerRef.current = setInterval(() => {
      void fetchLatestSession();
    }, 3000);

    return () => {
      stopRerunPoller();
    };
  }, [mappingSession?.session?.active_rerun_id]);

  const ensureSession = async () => {
    if (mappingSession) {
      return mappingSession.session.session_id;
    }

    const res = await fetch(`/api/jobs/${jobId}/mapping-sessions`, {
      method: "POST",
      headers: {
        ...authHeaders,
        "Content-Type": "application/json",
      },
      body: JSON.stringify({ title: "Manual mapping session" }),
    });

    if (!res.ok) {
      throw new Error("Could not create mapping session");
    }

    const detail = (await res.json()) as MappingSession;
    setMappingSession(detail);
    setReruns(detail.reruns ?? []);
    setSelectedCheckpointId(detail.session.current_checkpoint_id ?? null);
    syncEditorToSource(editorSource, detail, snapshot);
    return detail.session.session_id;
  };

  const handleRun = async () => {
    setIsRunning(true);
    setTraceEntries([]);
    setDraftSummary("");
    try {
      const res = await fetch(`/api/jobs/${jobId}/orchestration/run`, {
        method: "POST",
        headers: authHeaders,
      });
      if (res.ok) {
        const data = (await res.json()) as OrchestrationSnapshotResponse;
        appendTrace({
          role: "system",
          phase: "starting",
          title: "Run accepted",
          content: "Orchestration request accepted. Waiting for live progress...",
        });
        setSnapshot(data);
        if (!eventSourceRef.current) {
          startSseStream();
        }
        pushFeedback("Orchestration started.");
      } else if (res.status === 404) {
        pushFeedback("Orchestration endpoint is not available yet.");
      } else {
        const data = await res.json().catch(() => ({}));
        pushFeedback(data.detail || "Failed to start orchestration.");
      }
    } catch {
      pushFeedback("Cannot reach orchestration endpoint.");
    } finally {
      setIsRunning(false);
    }
  };

  const handleApply = async () => {
    if (!mappingJson.trim()) {
      pushFeedback("Mapping editor is empty.");
      return;
    }

    let parsed: Record<string, unknown>;
    try {
      parsed = JSON.parse(mappingJson) as Record<string, unknown>;
    } catch {
      pushFeedback("Mapping profile JSON is invalid.");
      return;
    }

    setIsApplying(true);
    try {
      const sessionId = await ensureSession();
      const res = await fetch(
        `/api/jobs/${jobId}/mapping-sessions/${sessionId}/checkpoints`,
        {
          method: "POST",
          headers: {
            ...authHeaders,
            "Content-Type": "application/json",
          },
          body: JSON.stringify({
            mapping_profile: parsed,
            author: "manual",
            summary: "Manual edit from orchestration workspace",
          }),
        },
      );

      if (res.ok) {
        const detail = (await res.json()) as MappingSession;
        setMappingSession(detail);
        setReruns(detail.reruns ?? []);
        setEditorSource("checkpoint");
        setSelectedCheckpointId(detail.session.current_checkpoint_id ?? null);
        syncEditorToSource("checkpoint", detail, snapshot);
        pushFeedback("Manual checkpoint saved.");
      } else if (res.status === 404) {
        pushFeedback("Mapping session endpoint is not available yet.");
      } else {
        const data = await res.json().catch(() => ({}));
        pushFeedback(data.detail || "Failed to save manual checkpoint.");
      }
    } catch {
      pushFeedback("Cannot save manual checkpoint.");
    } finally {
      setIsApplying(false);
    }
  };

  const handleRestore = async (checkpointId: string) => {
    if (!mappingSession) {
      return;
    }
    setIsRestoring(true);
    try {
      const res = await fetch(
        `/api/jobs/${jobId}/mapping-sessions/${mappingSession.session.session_id}/restore`,
        {
          method: "POST",
          headers: {
            ...authHeaders,
            "Content-Type": "application/json",
          },
          body: JSON.stringify({ checkpoint_id: checkpointId }),
        },
      );
      if (res.ok) {
        const detail = (await res.json()) as MappingSession;
        setMappingSession(detail);
        setReruns(detail.reruns ?? []);
        setEditorSource("checkpoint");
        setSelectedCheckpointId(checkpointId);
        syncEditorToSource("checkpoint", detail, snapshot);
        pushFeedback(`Restored checkpoint ${checkpointId.slice(0, 8)}.`);
      } else if (res.status === 404) {
        pushFeedback("Restore endpoint is not available yet.");
      } else {
        pushFeedback("Failed to restore checkpoint.");
      }
    } catch {
      pushFeedback("Cannot restore checkpoint.");
    } finally {
      setIsRestoring(false);
    }
  };

  const handleTriggerRerun = async () => {
    if (!selectedCheckpointId) {
      pushFeedback("Save or restore a checkpoint first to use as the rerun source.");
      return;
    }

    let sessionId = mappingSession?.session.session_id;
    try {
      sessionId = await ensureSession();
    } catch {
      pushFeedback("Cannot create mapping session.");
      return;
    }

    if (!sessionId) {
      return;
    }

    setIsTriggeringRerun(true);
    try {
      const res = await fetch(
        `/api/jobs/${jobId}/mapping-sessions/${sessionId}/reruns`,
        {
          method: "POST",
          headers: {
            ...authHeaders,
            "Content-Type": "application/json",
          },
          body: JSON.stringify({ checkpoint_id: selectedCheckpointId }),
        },
      );

      if (res.ok) {
        const rerun = (await res.json()) as RerunResponse;
        setReruns((previous) => [rerun, ...previous]);
        setSelectedCheckpointId(null);
        pushFeedback(`Rerun v${rerun.version} triggered.`);
        void fetchLatestSession();
      } else if (res.status === 404) {
        pushFeedback("Rerun endpoint is not available yet.");
      } else if (res.status === 409) {
        const data = await res.json().catch(() => ({}));
        pushFeedback(data.detail || "Another rerun is already active.");
      } else {
        const data = await res.json().catch(() => ({}));
        pushFeedback(data.detail || "Failed to trigger rerun.");
      }
    } catch {
      pushFeedback("Cannot reach rerun endpoint.");
    } finally {
      setIsTriggeringRerun(false);
    }
  };

  const completedRerun = reruns.find(
    (r) => r.rerun_id === mappingSession?.session?.latest_completed_rerun_id,
  );

  return (
    <div className="flex flex-col gap-4 overflow-y-auto">
      {feedback && (
        <div className="flex items-center gap-2 rounded-lg border border-accent/20 bg-accent-dim px-3 py-2 text-xs text-accent">
          <AlertCircle className="h-3.5 w-3.5 shrink-0" />
          <span>{feedback}</span>
        </div>
      )}

      <div className="flex flex-col gap-3 rounded-xl border border-slate-800 bg-slate-900/20 p-4">
        <div className="flex items-center justify-between">
          <h4 className="flex items-center gap-1.5 text-xs font-semibold uppercase tracking-wider text-slate-300">
            <Bot className="h-3.5 w-3.5" />
            Orchestrator Summary
          </h4>
          {snapshot && (
            <span className="rounded border border-slate-800 bg-slate-950 px-2 py-0.5 text-[10px] font-semibold uppercase tracking-wide text-slate-400">
              {snapshot.status}
            </span>
          )}
        </div>

        {isLoadingSnapshot ? (
          <div className="flex items-center justify-center py-6">
            <Loader2 className="h-5 w-5 animate-spin text-slate-500" />
          </div>
        ) : snapshot ? (
          <div className="flex flex-col gap-2 text-xs">
            <div className="rounded-lg border border-slate-800 bg-slate-950/60 p-3">
              <div className="text-[10px] font-semibold uppercase tracking-wide text-slate-500">
                Decision
              </div>
              <p className="mt-1 font-medium text-slate-200">
                {snapshot.decision || "No decision recorded yet"}
              </p>
            </div>

            {(snapshot.summary || draftSummary) && (
              <div className="rounded-lg border border-slate-800 bg-slate-950/60 p-3">
                <div className="text-[10px] font-semibold uppercase tracking-wide text-slate-500">
                  Summary
                </div>
                <p className="mt-1 leading-relaxed text-slate-300">
                  {snapshot.summary || draftSummary}
                </p>
              </div>
            )}

            <div className="grid grid-cols-1 gap-2 sm:grid-cols-2">
              <div className="rounded-lg border border-slate-800 bg-slate-950/60 p-3">
                <div className="text-[10px] font-semibold uppercase tracking-wide text-slate-500">
                  Confidence
                </div>
                <p className="mt-1 font-mono text-accent">
                  {snapshot.metadata?.confidence != null
                    ? `${Math.round(Number(snapshot.metadata.confidence) * 100)}%`
                    : "N/A"}
                </p>
              </div>
              <div className="rounded-lg border border-slate-800 bg-slate-950/60 p-3">
                <div className="text-[10px] font-semibold uppercase tracking-wide text-slate-500">
                  Current Phase
                </div>
                <p className="mt-1 text-slate-300">
                  {String(snapshot.metadata?.current_phase || "idle")}
                </p>
              </div>
            </div>

            <div className="rounded-lg border border-slate-800 bg-slate-950/60 p-3">
              <div className="text-[10px] font-semibold uppercase tracking-wide text-slate-500">
                Risks
              </div>
              <p className="mt-1 text-slate-300">
                {Array.isArray(snapshot.metadata?.risks)
                  ? (snapshot.metadata?.risks as string[]).join(", ") || "None"
                  : "None"}
              </p>
            </div>

            {snapshot.capture_guidance?.suggestions?.length ? (
              <div className="rounded-lg border border-sky-500/20 bg-sky-500/10 p-3 text-sky-200">
                <div className="text-[10px] font-semibold uppercase tracking-wide text-sky-400">
                  Capture Guidance
                </div>
                <ul className="mt-2 flex list-disc flex-col gap-1 pl-4 text-xs">
                  {snapshot.capture_guidance.suggestions.map((suggestion, index) => (
                    <li key={`guidance-${index}`}>{suggestion}</li>
                  ))}
                </ul>
              </div>
            ) : null}
          </div>
        ) : snapshotError ? (
          <p className="text-xs italic text-slate-500">{snapshotError}</p>
        ) : (
          <p className="text-xs italic text-slate-500">
            No orchestration has been run for this job yet.
          </p>
        )}

        <button
          onClick={() => void handleRun()}
          disabled={isRunning}
          className="flex items-center justify-center gap-2 rounded-lg bg-accent px-3 py-2 text-xs font-bold text-slate-950 transition-colors hover:bg-accent-hover disabled:opacity-50"
        >
          {isRunning ? (
            <>
              <Loader2 className="h-3.5 w-3.5 animate-spin" />
              Running...
            </>
          ) : (
            <>
              <Play className="h-3.5 w-3.5 fill-slate-950" />
              Run Orchestration
            </>
          )}
        </button>
      </div>

      <div className="flex flex-col gap-3 rounded-xl border border-slate-800 bg-slate-900/20 p-4">
        <div className="flex items-center justify-between">
          <h4 className="text-xs font-semibold uppercase tracking-wider text-slate-300">
            Live Execution Trace
          </h4>
          <span className="text-[10px] text-slate-500">
            {traceEntries.length} event{traceEntries.length === 1 ? "" : "s"}
          </span>
        </div>
        {traceEntries.length ? (
          <ul className="flex max-h-64 flex-col gap-2 overflow-y-auto rounded-lg border border-slate-800 bg-slate-950/60 p-3 text-xs text-slate-300">
            {traceEntries.map((entry, index) => {
              const Icon =
                entry.role === "ai"
                  ? Bot
                  : entry.role === "tool"
                    ? Wrench
                    : entry.role === "decision"
                      ? Check
                      : AlertCircle;
              const badgeClass =
                entry.role === "ai"
                  ? "border-fuchsia-500/30 bg-fuchsia-500/10 text-fuchsia-200"
                  : entry.role === "tool"
                    ? "border-cyan-500/30 bg-cyan-500/10 text-cyan-200"
                    : entry.role === "decision"
                      ? "border-emerald-500/30 bg-emerald-500/10 text-emerald-200"
                      : "border-slate-700 bg-slate-900 text-slate-300";

              return (
                <li
                  key={`orchestration-trace-${index}`}
                  className={`rounded-lg border p-3 ${entry.heartbeat ? "opacity-70" : ""} ${badgeClass}`}
                >
                  <div className="flex items-start gap-2">
                    <Icon className="mt-0.5 h-3.5 w-3.5 shrink-0" />
                    <div className="min-w-0 flex-1">
                      <div className="flex flex-wrap items-center gap-2">
                        <span className="text-[10px] font-semibold uppercase tracking-wide">
                          {entry.role} · {entry.phase}
                        </span>
                        <span className="text-[11px] font-semibold">{entry.title}</span>
                        {entry.tool_name ? (
                          <span className="rounded border border-current/20 px-1.5 py-0.5 text-[10px] opacity-80">
                            {entry.tool_name}
                          </span>
                        ) : null}
                      </div>
                      <p className="mt-1 leading-relaxed text-current/90">{entry.content}</p>
                    </div>
                  </div>
                </li>
              );
            })}
          </ul>
        ) : (
          <p className="text-xs italic text-slate-500">
            Start orchestration to stream phase-by-phase trace events here.
          </p>
        )}
      </div>

      <div className="flex flex-col gap-3 rounded-xl border border-slate-800 bg-slate-900/20 p-4">
        <h4 className="text-xs font-semibold uppercase tracking-wider text-slate-300">
          Checkpoint Timeline
        </h4>

        {isLoadingSession ? (
          <div className="flex items-center justify-center py-4">
            <Loader2 className="h-4 w-4 animate-spin text-slate-500" />
          </div>
        ) : sessionError ? (
          <p className="text-xs italic text-slate-500">{sessionError}</p>
        ) : mappingSession?.checkpoints.length ? (
          <div className="flex flex-col gap-2">
            {mappingSession.checkpoints.map((checkpoint) => {
              const isCurrent =
                checkpoint.checkpoint_id ===
                mappingSession.session.current_checkpoint_id;
              return (
                <div
                  key={checkpoint.checkpoint_id}
                  className={`flex items-center gap-3 rounded-lg border px-3 py-2 text-xs ${
                    isCurrent
                      ? "border-accent/30 bg-accent-dim"
                      : "border-slate-800 bg-slate-950/50"
                  }`}
                >
                  <div className="flex min-w-0 flex-1 flex-col">
                    <span className="truncate font-semibold text-slate-200">
                      {checkpoint.summary || checkpoint.author}
                    </span>
                    <span className="text-[10px] text-slate-500">
                      {new Date(checkpoint.created_at).toLocaleTimeString()} · {checkpoint.author}
                    </span>
                  </div>
                  {isCurrent ? (
                    <Check className="h-3.5 w-3.5 shrink-0 text-emerald-500" />
                  ) : null}
                  <button
                    onClick={() => void handleRestore(checkpoint.checkpoint_id)}
                    disabled={isRestoring}
                    className="flex shrink-0 items-center gap-1 rounded border border-slate-800 bg-slate-950 px-2 py-1 text-[10px] font-semibold text-slate-400 transition-colors hover:border-slate-700 hover:text-slate-200 disabled:opacity-50"
                  >
                    <RotateCcw className="h-3 w-3" />
                    Restore
                  </button>
                </div>
              );
            })}
          </div>
        ) : (
          <p className="text-xs italic text-slate-500">
            No checkpoints yet. Create a manual checkpoint to start iterating.
          </p>
        )}
      </div>

      <div className="flex flex-col gap-3 rounded-xl border border-slate-800 bg-slate-900/20 p-4">
        <div className="flex items-center justify-between gap-3">
          <div>
            <h4 className="text-xs font-semibold uppercase tracking-wider text-slate-300">
              Manual Mapping Editor
            </h4>
            <span className="text-[10px] text-slate-500">
              Save as a reversible checkpoint
            </span>
          </div>
          <div className="flex items-center gap-2">
            <button
              type="button"
              onClick={() => {
                setEditorSource("checkpoint");
                syncEditorToSource("checkpoint", mappingSession, snapshot);
              }}
              className={`rounded border px-2 py-1 text-[10px] font-semibold transition-colors ${
                editorSource === "checkpoint"
                  ? "border-accent/40 bg-accent-dim text-accent"
                  : "border-slate-800 bg-slate-950 text-slate-400 hover:border-slate-700 hover:text-slate-200"
              }`}
            >
              Current checkpoint
            </button>
            <button
              type="button"
              onClick={() => {
                if (!hasTunedProfile) {
                  return;
                }
                setEditorSource("tuned");
                syncEditorToSource("tuned", mappingSession, snapshot);
              }}
              disabled={!hasTunedProfile}
              className={`rounded border px-2 py-1 text-[10px] font-semibold transition-colors ${
                editorSource === "tuned"
                  ? "border-cyan-500/40 bg-cyan-500/10 text-cyan-200"
                  : "border-slate-800 bg-slate-950 text-slate-400 hover:border-slate-700 hover:text-slate-200 disabled:cursor-not-allowed disabled:opacity-50"
              }`}
              title={hasTunedProfile ? "View orchestrator-tuned profile" : "No tuned profile suggested yet"}
            >
              Tuned profile
            </button>
          </div>
        </div>

        <div className="rounded-lg border border-slate-800 bg-slate-950/40 px-3 py-2 text-[11px] text-slate-400">
          {editorSource === "tuned" && tunedProfileJson
            ? "Viewing the orchestrator-suggested tuned profile. Edit it here, then save a manual checkpoint if you want to keep a variant."
            : "Viewing the current checkpoint profile. Switch to the tuned profile when orchestration suggests one."}
        </div>

        <textarea
          value={mappingJson}
          onChange={(event) => setMappingJson(event.target.value)}
          rows={10}
          spellCheck={false}
          placeholder={`{\n  "handedness": "right",\n  "workspace_scale": 1.0,\n  "depth_scale": 0.8\n}`}
          className="w-full resize-y rounded-lg border border-slate-800 bg-slate-950/80 p-3 font-mono text-xs text-slate-200 outline-none transition-colors placeholder:text-slate-600 focus:border-accent"
        />

        <button
          onClick={() => void handleApply()}
          disabled={isApplying}
          className="flex items-center justify-center gap-2 rounded-lg border border-slate-800 bg-slate-950 px-3 py-2 text-xs font-semibold text-slate-300 transition-colors hover:border-slate-700 hover:text-slate-100 disabled:opacity-50"
        >
          {isApplying ? (
            <>
              <Loader2 className="h-3.5 w-3.5 animate-spin" />
              Saving...
            </>
          ) : (
            <>
              <Save className="h-3.5 w-3.5" />
              Save Manual Checkpoint
              <ChevronRight className="h-3.5 w-3.5" />
            </>
          )}
        </button>

        {selectedCheckpointId && (
          <div className="flex items-center justify-between rounded-lg border border-accent/20 bg-accent-dim px-3 py-2 text-xs">
            <span className="text-accent">
              Checkpoint {selectedCheckpointId.slice(0, 8)} selected as rerun source
            </span>
            <button
              onClick={() => void handleTriggerRerun()}
              disabled={isTriggeringRerun}
              className="flex shrink-0 items-center gap-1.5 rounded bg-accent px-3 py-1.5 text-[10px] font-bold text-slate-950 transition-colors hover:bg-accent-hover disabled:opacity-50"
            >
              {isTriggeringRerun ? (
                <>
                  <Loader2 className="h-3 w-3 animate-spin" />
                  Triggering...
                </>
              ) : (
                <>
                  <GitBranch className="h-3 w-3" />
                  Trigger Rerun
                </>
              )}
            </button>
          </div>
        )}
      </div>

      <div className="flex flex-col gap-3 rounded-xl border border-slate-800 bg-slate-900/20 p-4">
        <h4 className="flex items-center gap-1.5 text-xs font-semibold uppercase tracking-wider text-slate-300">
          <GitBranch className="h-3.5 w-3.5" />
          Rerun Comparison
        </h4>

        <div className="grid grid-cols-1 gap-2 sm:grid-cols-2 text-xs">
          <div className="rounded-lg border border-slate-800 bg-slate-950/60 p-3">
            <div className="text-[10px] font-semibold uppercase tracking-wide text-slate-500">
              Orchestrator Baseline
            </div>
            <p className="mt-1 text-slate-300">
              {snapshot?.decision ? (
                <>
                  {snapshot.decision}
                  {snapshot.metadata?.confidence != null
                    ? ` (${Math.round(Number(snapshot.metadata.confidence) * 100)}%)`
                    : ""}
                </>
              ) : (
                "No orchestration run yet"
              )}
            </p>
          </div>
          <div className="rounded-lg border border-slate-800 bg-slate-950/60 p-3">
            <div className="text-[10px] font-semibold uppercase tracking-wide text-slate-500">
              Latest Rerun
            </div>
            {completedRerun ? (
              <p className="mt-1 text-slate-300">
                v{completedRerun.version} ·{" "}
                <span className={`font-semibold ${
                  completedRerun.status === "completed" ? "text-emerald-400" :
                  completedRerun.status === "failed" ? "text-red-400" :
                  "text-amber-400"
                }`}>
                  {completedRerun.status}
                </span>
                {completedRerun.summary ? (
                  <> &mdash; {completedRerun.summary.slice(0, 60)}</>
                ) : null}
              </p>
            ) : mappingSession?.session?.active_rerun_id ? (
              <p className="mt-1 flex items-center gap-1.5 text-amber-400">
                <Loader2 className="h-3 w-3 animate-spin" />
                Rerun in progress
              </p>
            ) : (
              <p className="mt-1 text-slate-500">No reruns yet</p>
            )}
          </div>
        </div>

        {(completedRerun?.summary || completedRerun?.status) && (
          <div className="rounded-lg border border-slate-800 bg-slate-950/60 p-3 text-xs">
            <div className="text-[10px] font-semibold uppercase tracking-wide text-slate-500 mb-1">
              Rerun Detail
            </div>
            {completedRerun?.summary && (
              <p className="text-slate-300 leading-relaxed">
                {completedRerun.summary}
              </p>
            )}
            {completedRerun?.error && (
              <p className="mt-1 text-red-400">{completedRerun.error}</p>
            )}
            {completedRerun?.completed_at && (
              <p className="mt-1 flex items-center gap-1 text-slate-500">
                <Clock className="h-3 w-3" />
                Completed {new Date(completedRerun.completed_at).toLocaleString()}
              </p>
            )}
          </div>
        )}
      </div>

      <div className="flex flex-col gap-3 rounded-xl border border-slate-800 bg-slate-900/20 p-4">
        <div className="flex items-center justify-between">
          <h4 className="text-xs font-semibold uppercase tracking-wider text-slate-300">
            Rerun Timeline
          </h4>
          <span className="text-[10px] text-slate-500">
            {reruns.length} rerun{reruns.length === 1 ? "" : "s"}
          </span>
        </div>

        {mappingSession?.session?.active_rerun_id ? (
          <div className="flex items-center gap-2 rounded-lg border border-amber-500/20 bg-amber-500/10 px-3 py-2 text-xs text-amber-300">
            <Loader2 className="h-3.5 w-3.5 shrink-0 animate-spin" />
            Rerun active — polling for updates...
          </div>
        ) : null}

        {reruns.length ? (
          <ul className="flex max-h-64 flex-col gap-2 overflow-y-auto pr-0.5 text-xs">
            {reruns.map((rerun) => {
              const StatusIcon =
                rerun.status === "completed"
                  ? Check
                  : rerun.status === "failed"
                    ? XCircle
                    : rerun.status === "running"
                      ? Loader2
                      : Clock;
              const statusColor =
                rerun.status === "completed"
                  ? "border-emerald-500/20 bg-emerald-500/5 text-emerald-300"
                  : rerun.status === "failed"
                    ? "border-red-500/20 bg-red-500/5 text-red-300"
                    : rerun.status === "running" || rerun.status === "queued"
                      ? "border-amber-500/20 bg-amber-500/5 text-amber-300"
                      : "border-slate-700 bg-slate-900 text-slate-400";
              const iconClass =
                rerun.status === "running"
                  ? "mt-0.5 h-3.5 w-3.5 shrink-0 animate-spin"
                  : "mt-0.5 h-3.5 w-3.5 shrink-0";

              return (
                <li
                  key={rerun.rerun_id}
                  className={`rounded-lg border px-3 py-2 ${statusColor}`}
                >
                  <div className="flex items-start gap-2">
                    <StatusIcon className={iconClass} />
                    <div className="min-w-0 flex-1">
                      <div className="flex flex-wrap items-center gap-2">
                        <span className="font-semibold">v{rerun.version}</span>
                        <span className="text-[10px] font-semibold uppercase tracking-wide opacity-80">
                          {rerun.status}
                        </span>
                        <span className="text-[10px] opacity-60">
                          src:{rerun.source_checkpoint_id.slice(0, 8)}
                        </span>
                      </div>
                      {rerun.summary || rerun.error ? (
                        <p className="mt-1 leading-relaxed opacity-90">
                          {rerun.summary || rerun.error}
                        </p>
                      ) : rerun.status !== "running" ? (
                        <p className="mt-1 text-slate-500">No summary available</p>
                      ) : null}
                      <div className="mt-1 flex flex-wrap gap-x-3 text-[10px] opacity-60">
                        <span>Created {new Date(rerun.created_at).toLocaleString()}</span>
                        {rerun.completed_at ? (
                          <span>
                            Completed {new Date(rerun.completed_at).toLocaleString()}
                          </span>
                        ) : null}
                      </div>
                    </div>
                  </div>
                </li>
              );
            })}
          </ul>
        ) : (
          <p className="text-xs italic text-slate-500">
            Trigger a rerun from a saved or restored checkpoint to see versioned results here.
          </p>
        )}
      </div>
    </div>
  );
}
