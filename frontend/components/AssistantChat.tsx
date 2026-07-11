"use client";

import React, { useState, useEffect, useRef } from "react";
import { MessageSquare, Send, Bot, User, Wrench, AlertCircle, Loader2, Plus, ArrowLeft } from "lucide-react";

interface AssistantChatProps {
  jobId: string;
  token: string;
}

interface Session {
  session_id: string;
  title: string;
  status: string;
  created_at: string;
}

interface Message {
  role: "user" | "assistant" | "tool";
  content: string;
  name?: string;
  metadata?: any;
}

export default function AssistantChat({ jobId, token }: AssistantChatProps) {
  const [sessions, setSessions] = useState<Session[]>([]);
  const [selectedSessionId, setSelectedSessionId] = useState<string | null>(null);
  const [messages, setMessages] = useState<Message[]>([]);
  const [isLoadingSessions, setIsLoadingSessions] = useState(false);
  const [isLoadingTranscript, setIsLoadingTranscript] = useState(false);
  const [isSending, setIsSending] = useState(false);
  const [input, setInput] = useState("");
  const [chatStatus, setChatStatus] = useState<string>("idle");

  const eventSourceRef = useRef<EventSource | null>(null);
  const messagesEndRef = useRef<HTMLDivElement>(null);

  const closeStream = () => {
    if (eventSourceRef.current) {
      eventSourceRef.current.close();
      eventSourceRef.current = null;
    }
  };

  useEffect(() => {
    fetchSessions();
    return () => closeStream();
  }, [jobId]);

  useEffect(() => {
    if (selectedSessionId) {
      fetchTranscript(selectedSessionId);
    } else {
      setMessages([]);
      closeStream();
    }
  }, [selectedSessionId]);

  useEffect(() => {
    // Scroll to bottom whenever messages or chat status changes
    messagesEndRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [messages, chatStatus]);

  const fetchSessions = async () => {
    setIsLoadingSessions(true);
    try {
      const res = await fetch(`/api/jobs/${jobId}/assistant/sessions`, {
        headers: { Authorization: `Bearer ${token}` },
      });
      if (res.ok) {
        const data = await res.json();
        setSessions(data.sessions || []);
      }
    } catch (err) {
      console.error("Failed to fetch assistant sessions", err);
    } finally {
      setIsLoadingSessions(false);
    }
  };

  const fetchTranscript = async (sessionId: string) => {
    setIsLoadingTranscript(true);
    closeStream();
    try {
      const res = await fetch(`/api/jobs/${jobId}/assistant/sessions/${sessionId}`, {
        headers: { Authorization: `Bearer ${token}` },
      });
      if (res.ok) {
        const data = await res.json();
        setSessions((prev) =>
          prev.map((s) => (s.session_id === sessionId ? data.session : s))
        );
        setMessages(data.messages || []);
        setChatStatus(data.session.status);

        if (data.session.status === "running") {
          startSseStream(sessionId);
        }
      }
    } catch (err) {
      console.error("Failed to fetch transcript", err);
    } finally {
      setIsLoadingTranscript(false);
    }
  };

  const handleCreateSession = async () => {
    const title = prompt("Enter a title for this chat session:", `Session ${sessions.length + 1}`);
    if (title === null) return;
    
    try {
      const res = await fetch(`/api/jobs/${jobId}/assistant/sessions`, {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          Authorization: `Bearer ${token}`,
        },
        body: JSON.stringify({ title: title.trim() || undefined }),
      });
      if (res.ok) {
        const data = await res.json();
        setSessions((prev) => [data.session, ...prev]);
        setSelectedSessionId(data.session.session_id);
      }
    } catch (err) {
      console.error("Failed to create assistant session", err);
    }
  };

  const handleSendMessage = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!input.trim() || !selectedSessionId || isSending) return;

    const userContent = input.trim();
    setInput("");
    setIsSending(true);
    setChatStatus("running");

    // Optimistically add user message
    setMessages((prev) => [...prev, { role: "user", content: userContent }]);

    try {
      const res = await fetch(`/api/jobs/${jobId}/assistant/sessions/${selectedSessionId}/messages`, {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          Authorization: `Bearer ${token}`,
        },
        body: JSON.stringify({ content: userContent }),
      });

      if (res.ok) {
        // Start streaming response
        startSseStream(selectedSessionId);
      } else {
        throw new Error("Failed to send message");
      }
    } catch (err) {
      console.error(err);
      setChatStatus("failed");
      setIsSending(false);
    }
  };

  const startSseStream = (sessionId: string) => {
    closeStream();
    
    // We append a temporary bot message container that we fill as tokens arrive
    let streamAdded = false;

    const source = new EventSource(`/api/jobs/${jobId}/assistant/sessions/${sessionId}/stream?token=${token}`);
    eventSourceRef.current = source;

    source.addEventListener("status", (e) => {
      try {
        const data = JSON.parse(e.data);
        setChatStatus(data.status);
      } catch {}
    });

    // Listen to live tool execution
    source.addEventListener("tool", (e) => {
      try {
        const data = JSON.parse(e.data);
        setMessages((prev) => [
          ...prev,
          {
            role: "tool",
            name: data.tool_name,
            content: JSON.stringify(data.result, null, 2),
            metadata: { arguments: data.arguments },
          },
        ]);
        streamAdded = false; // Reset to allow assistant response container after tools
      } catch {}
    });

    // Listen to streamed assistant response tokens
    source.addEventListener("token", (e) => {
      try {
        const data = JSON.parse(e.data);
        const text = data.text;
        setIsSending(false); // Stop loading spinner once tokens flow

        setMessages((prev) => {
          const list = [...prev];
          const last = list[list.length - 1];
          
          if (last && last.role === "assistant" && streamAdded) {
            last.content += text;
            return list;
          } else {
            streamAdded = true;
            return [...list, { role: "assistant", content: text }];
          }
        });
      } catch {}
    });

    source.addEventListener("done", () => {
      setChatStatus("idle");
      closeStream();
      setIsSending(false);
    });

    source.addEventListener("error", (e) => {
      console.error("SSE stream error", e);
      setChatStatus("failed");
      closeStream();
      setIsSending(false);
    });
  };

  // Helper for rendering tool result JSON cleanly
  const ToolDetails = ({ name, content, args }: { name: string; content: string; args: any }) => {
    const [isOpen, setIsOpen] = useState(false);
    return (
      <div className="my-2 rounded-lg border border-slate-800 bg-slate-950/60 p-2.5 font-mono text-[10px] text-slate-400">
        <button
          type="button"
          onClick={() => setIsOpen(!isOpen)}
          className="flex w-full items-center justify-between text-left font-semibold text-accent hover:text-accent-hover transition-colors"
        >
          <span className="flex items-center gap-1.5">
            <Wrench className="h-3.5 w-3.5" /> Called Tool: {name}
          </span>
          <span className="text-[9px] uppercase tracking-wider text-slate-500">
            {isOpen ? "Hide Output" : "View Output"}
          </span>
        </button>
        {isOpen && (
          <div className="mt-2 border-t border-slate-900 pt-2 space-y-1">
            {args && Object.keys(args).length > 0 && (
              <div>
                <span className="text-slate-500">Arguments:</span>
                <pre className="mt-0.5 overflow-x-auto text-[9px] text-slate-300">{JSON.stringify(args, null, 2)}</pre>
              </div>
            )}
            <div>
              <span className="text-slate-500">Result:</span>
              <pre className="mt-0.5 max-h-36 overflow-auto text-[9px] text-slate-300 leading-normal">{content}</pre>
            </div>
          </div>
        )}
      </div>
    );
  };

  const parseMessageMarkdown = (md: string) => {
    if (!md) return "";
    return md
      .replace(/\*\*(.*?)\*\*/g, '<strong class="font-bold text-slate-200">$1</strong>')
      .replace(/`(.*?)`/g, '<code class="rounded bg-slate-950 px-1 py-0.5 font-mono text-[10px] text-accent">$1</code>')
      .replace(/\n/g, "<br />");
  };

  // ── SESSIONS LIST VIEW ──
  if (!selectedSessionId) {
    return (
      <div className="flex h-full flex-col gap-4 rounded-xl border border-slate-800 bg-slate-900/20 p-5">
        <div className="flex items-center justify-between">
          <h3 className="text-xs font-bold tracking-wider text-slate-400 uppercase">
            AI Review Assistant Chat
          </h3>
          <button
            onClick={handleCreateSession}
            className="flex items-center gap-1 rounded bg-accent px-2 py-1 text-[10px] font-bold text-slate-950 hover:bg-accent-hover transition-all"
          >
            <Plus className="h-3.5 w-3.5" /> New Session
          </button>
        </div>

        {isLoadingSessions ? (
          <div className="flex flex-1 items-center justify-center">
            <Loader2 className="h-6 w-6 animate-spin text-slate-600" />
          </div>
        ) : sessions.length === 0 ? (
          <div className="flex flex-1 flex-col items-center justify-center text-center text-slate-500 p-8 border border-dashed border-slate-800 rounded-lg">
            <MessageSquare className="h-6 w-6 text-slate-600 mb-2" />
            <p className="text-[11px] font-medium">No active chat sessions.</p>
            <p className="text-[10px] text-slate-600 mt-1 max-w-xs leading-normal">
              Start an interactive chat session to query the agent about specific keypoint metrics, trajectories, or static check details.
            </p>
          </div>
        ) : (
          <div className="flex flex-col gap-2 overflow-y-auto max-h-[420px] pr-1">
            {sessions.map((s) => (
              <button
                key={s.session_id}
                onClick={() => setSelectedSessionId(s.session_id)}
                className="flex items-center justify-between rounded-lg border border-slate-850 bg-slate-950/40 p-3 text-left hover:bg-slate-950/80 hover:border-slate-800 transition-all"
              >
                <div className="flex flex-col gap-1 min-w-0">
                  <span className="truncate text-xs font-semibold text-slate-300">
                    {s.title}
                  </span>
                  <span className="text-[9px] font-mono text-slate-600">
                    ID: {s.session_id.slice(0, 8)}
                  </span>
                </div>
                <span className={`rounded-full px-1.5 py-0.5 text-[8px] font-bold uppercase tracking-wider ${
                  s.status === "running" ? "bg-accent-dim text-accent animate-pulse" : "bg-slate-800 text-slate-500"
                }`}>
                  {s.status}
                </span>
              </button>
            ))}
          </div>
        )}
      </div>
    );
  }

  // ── TRANSCRIPT / CHAT WINDOW VIEW ──
  const activeSession = sessions.find((s) => s.session_id === selectedSessionId);

  return (
    <div className="flex h-full min-h-[480px] flex-col rounded-xl border border-slate-800 bg-slate-900/20 overflow-hidden">
      {/* Chat Header */}
      <div className="flex items-center gap-2 border-b border-slate-900 bg-slate-950/40 p-3 px-4">
        <button
          onClick={() => setSelectedSessionId(null)}
          className="rounded p-1 text-slate-500 hover:bg-slate-900 hover:text-slate-300"
        >
          <ArrowLeft className="h-4 w-4" />
        </button>
        <div className="flex flex-col min-w-0">
          <span className="truncate text-xs font-bold text-slate-200">
            {activeSession?.title}
          </span>
          <span className="text-[9px] font-mono text-slate-500">
            Assistant: {chatStatus}
          </span>
        </div>
        {chatStatus === "running" && (
          <Loader2 className="ml-auto h-4 w-4 animate-spin text-accent" />
        )}
      </div>

      {/* Chat Messages */}
      <div className="flex-1 overflow-y-auto p-4 space-y-3 max-h-[380px] min-h-[320px]">
        {isLoadingTranscript ? (
          <div className="flex h-full items-center justify-center">
            <Loader2 className="h-6 w-6 animate-spin text-slate-600" />
          </div>
        ) : (
          <>
            {messages.map((m, idx) => {
              if (m.role === "tool") {
                return (
                  <ToolDetails
                    key={`tool-${idx}`}
                    name={m.name || "unknown"}
                    content={m.content}
                    args={m.metadata?.arguments}
                  />
                );
              }

              const isBot = m.role === "assistant";
              return (
                <div
                  key={`msg-${idx}`}
                  className={`flex gap-2.5 max-w-[85%] ${isBot ? "mr-auto" : "ml-auto flex-row-reverse"}`}
                >
                  <div className={`flex h-6 w-6 shrink-0 items-center justify-center rounded-full ${
                    isBot ? "bg-accent-dim text-accent border border-accent/20" : "bg-slate-800 text-slate-300"
                  }`}>
                    {isBot ? <Bot className="h-3.5 w-3.5" /> : <User className="h-3.5 w-3.5" />}
                  </div>
                  <div className={`rounded-xl px-3 py-2 text-xs leading-normal font-sans border ${
                    isBot
                      ? "bg-slate-950/70 border-slate-900 text-slate-300"
                      : "bg-accent text-slate-950 border-accent/20 font-medium"
                  }`}>
                    {isBot ? (
                      <div dangerouslySetInnerHTML={{ __html: parseMessageMarkdown(m.content) }} />
                    ) : (
                      <span>{m.content}</span>
                    )}
                  </div>
                </div>
              );
            })}
            
            {isSending && (
              <div className="flex gap-2.5 mr-auto max-w-[85%]">
                <div className="flex h-6 w-6 shrink-0 items-center justify-center rounded-full bg-accent-dim text-accent animate-pulse">
                  <Bot className="h-3.5 w-3.5" />
                </div>
                <div className="rounded-xl border border-slate-900 bg-slate-950/70 px-3 py-2 text-xs text-slate-500 italic">
                  Agent reasoning and executing tools...
                </div>
              </div>
            )}
            
            <div ref={messagesEndRef} />
          </>
        )}
      </div>

      {/* Chat Input */}
      <form onSubmit={handleSendMessage} className="border-t border-slate-900 bg-slate-950/40 p-2.5 flex gap-2">
        <input
          type="text"
          placeholder="Ask about trajectory jumps, joint averages..."
          value={input}
          onChange={(e) => setInput(e.target.value)}
          disabled={chatStatus === "running" || isSending}
          className="flex-1 rounded-lg border border-slate-850 bg-slate-950/80 px-3 py-2 text-xs text-slate-100 placeholder-slate-500 outline-none focus:border-accent transition-all disabled:opacity-50"
        />
        <button
          type="submit"
          disabled={chatStatus === "running" || isSending || !input.trim()}
          className="flex h-8 w-8 items-center justify-center rounded-lg bg-accent text-slate-950 hover:bg-accent-hover transition-colors disabled:opacity-40"
        >
          <Send className="h-3.5 w-3.5" />
        </button>
      </form>
    </div>
  );
}
