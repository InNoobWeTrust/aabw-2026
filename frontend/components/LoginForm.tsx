"use client";

import React, { useState } from "react";
import { Lock, AlertCircle } from "lucide-react";

interface LoginFormProps {
  onLoginSuccess: (token: string, role: string, sessionId: string) => void;
}

export default function LoginForm({ onLoginSuccess }: LoginFormProps) {
  const [password, setPassword] = useState("");
  const [isLoading, setIsLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    setIsLoading(true);
    setError(null);

    try {
      const res = await fetch("/api/auth/login", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ password }),
      });

      const data = await res.json();
      if (!res.ok) {
        throw new Error(data.detail || "Authentication failed");
      }

      onLoginSuccess(data.access_token, data.role, data.judge_session_id);
    } catch (err: any) {
      setError(err.message || "Failed to authenticate");
    } finally {
      setIsLoading(false);
    }
  };

  return (
    <div className="flex min-h-screen items-center justify-center p-4">
      <div className="w-full max-w-md rounded-2xl border border-slate-800 bg-slate-900/40 p-8 text-center shadow-2xl backdrop-blur-xl">
        {/* Animated Robot Logo */}
        <div className="mx-auto mb-4 flex h-16 w-16 items-center justify-center rounded-xl bg-accent-dim">
          <svg
            viewBox="0 0 48 48"
            fill="none"
            xmlns="http://www.w3.org/2000/svg"
            className="h-10 w-10 stroke-accent"
            strokeWidth="2"
          >
            <rect width="48" height="48" rx="12" fill="none" className="opacity-15" />
            <path d="M14 24l6-8h8l6 8-6 8h-8l-6-8z" fill="none" />
            <circle cx="24" cy="24" r="3" className="fill-accent" />
            <line x1="24" y1="21" x2="24" y2="10" strokeLinecap="round" />
            <line x1="30" y1="18" x2="37" y2="14" strokeLinecap="round" />
            <line x1="18" y1="18" x2="11" y2="14" strokeLinecap="round" />
          </svg>
        </div>

        <h1 className="text-2xl font-bold tracking-tight text-slate-100">RoboData</h1>
        <p className="text-xs font-semibold text-accent tracking-wider uppercase mt-1">
          Phone Video → Robot Dataset
        </p>

        <form onSubmit={handleSubmit} className="mt-8 flex flex-col gap-4">
          <div className="relative">
            <span className="absolute left-3 top-3.5 text-slate-500">
              <Lock className="h-4 w-4" />
            </span>
            <input
              type="password"
              placeholder="Enter access password"
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              className="w-full rounded-lg border border-slate-800 bg-slate-950/70 py-3 pl-10 pr-4 text-sm text-slate-100 placeholder-slate-500 outline-none ring-accent focus:border-accent focus:ring-1 transition-all"
              required
            />
          </div>

          <button
            type="submit"
            disabled={isLoading}
            className="mt-2 w-full rounded-lg bg-accent py-3 text-sm font-bold text-slate-950 hover:bg-accent-hover active:scale-[0.99] disabled:cursor-not-allowed disabled:opacity-50 transition-all shadow-md"
          >
            {isLoading ? "Authenticating..." : "Authenticate"}
          </button>
        </form>

        {error && (
          <div className="mt-4 flex items-center gap-2 rounded-lg border border-rose-500/20 bg-rose-500/10 p-3 text-left text-xs text-rose-400">
            <AlertCircle className="h-4 w-4 shrink-0" />
            <span>{error}</span>
          </div>
        )}
      </div>
    </div>
  );
}
