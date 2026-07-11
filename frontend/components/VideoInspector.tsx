"use client";

import React, { useRef, useState, useEffect } from "react";
import { Play, Pause, RefreshCw, Maximize2, Minimize2, Video, Activity, RefreshCcw } from "lucide-react";

interface VideoInspectorProps {
  jobId: string;
  token: string;
  hasRobot: boolean;
  onTimeUpdate: (time: number, duration: number) => void;
  scrubTime: number | null;
}

export default function VideoInspector({
  jobId,
  token,
  hasRobot,
  onTimeUpdate,
  scrubTime,
}: VideoInspectorProps) {
  const originalRef = useRef<HTMLVideoElement>(null);
  const overlayRef = useRef<HTMLVideoElement>(null);
  const previewRef = useRef<HTMLVideoElement>(null);
  const simulationRef = useRef<HTMLVideoElement>(null);

  const [activeTab, setActiveTab] = useState<"grid" | "pose" | "retarget">("grid");
  const [isPlaying, setIsPlaying] = useState(false);
  const [currentTime, setCurrentTime] = useState(0);
  const [duration, setDuration] = useState(0);
  const [focusedVideo, setFocusedVideo] = useState<string | null>(null);

  const [feedsAvailable, setFeedsAvailable] = useState({
    overlay: true,
    preview: true,
    simulation: true,
  });

  const allVideos = [
    { id: "original", ref: originalRef, label: "Original Demonstration" },
    { id: "overlay", ref: overlayRef, label: "Skeleton Overlay (MediaPipe)" },
    { id: "preview", ref: previewRef, label: "Skeleton 3D Preview" },
    { id: "simulation", ref: simulationRef, label: "Robot Joint Simulation" },
  ];

  // Monitor scrub triggers from external parent (e.g. TrajectoryChart click)
  useEffect(() => {
    if (scrubTime !== null && originalRef.current) {
      originalRef.current.currentTime = scrubTime;
      allVideos.forEach((v) => {
        if (v.ref.current && v.id !== "original" && feedsAvailable[v.id as keyof typeof feedsAvailable]) {
          v.ref.current.currentTime = scrubTime;
        }
      });
    }
  }, [scrubTime]);

  // Synchronize playback positions
  const syncPlayback = (targetTime: number) => {
    allVideos.forEach((v) => {
      if (v.ref.current && v.id !== "original" && feedsAvailable[v.id as keyof typeof feedsAvailable]) {
        const diff = Math.abs(v.ref.current.currentTime - targetTime);
        if (diff > 0.15) {
          v.ref.current.currentTime = targetTime;
        }
      }
    });
  };

  const handlePlayToggle = () => {
    if (isPlaying) {
      allVideos.forEach((v) => v.ref.current?.pause());
      setIsPlaying(false);
    } else {
      const targetTime = originalRef.current?.currentTime ?? 0;
      syncPlayback(targetTime);
      allVideos.forEach((v) => {
        if (v.ref.current && (v.id === "original" || feedsAvailable[v.id as keyof typeof feedsAvailable])) {
          v.ref.current.play().catch(() => {});
        }
      });
      setIsPlaying(true);
    }
  };

  const handleVideoTimeUpdate = () => {
    if (originalRef.current) {
      const cur = originalRef.current.currentTime;
      const dur = originalRef.current.duration || 0;
      setCurrentTime(cur);
      setDuration(dur);
      onTimeUpdate(cur, dur);

      // Continuously sync secondary videos to avoid minor drift
      if (isPlaying && Math.floor(cur * 10) % 5 === 0) {
        syncPlayback(cur);
      }
    }
  };

  const handleVideoSeeked = () => {
    if (originalRef.current) {
      syncPlayback(originalRef.current.currentTime);
    }
  };

  const handleRestart = () => {
    allVideos.forEach((v) => {
      if (v.ref.current) {
        v.ref.current.currentTime = 0;
      }
    });
    setCurrentTime(0);
    if (isPlaying) {
      allVideos.forEach((v) => {
        if (v.ref.current && (v.id === "original" || feedsAvailable[v.id as keyof typeof feedsAvailable])) {
          v.ref.current.play().catch(() => {});
        }
      });
    }
  };

  const handleVideoError = (feedId: "overlay" | "preview" | "simulation") => {
    setFeedsAvailable((prev) => ({ ...prev, [feedId]: false }));
    // If the active tab was retarget and simulation fails, switch to grid
    if (feedId === "simulation" && activeTab === "retarget") {
      setActiveTab("grid");
    }
    // If overlay fails and pose tab was active, switch to grid
    if (feedId === "overlay" && activeTab === "pose") {
      setActiveTab("grid");
    }
  };

  // Build secure URLs
  const getUrl = (type: string) => {
    return `/api/jobs/${jobId}/video/${type}?token=${token}`;
  };

  return (
    <div className="flex flex-col gap-3">
      {/* Tab Switcher & Theater controls */}
      <div className="flex flex-wrap items-center justify-between gap-2 border-b border-slate-800 pb-2">
        <div className="flex gap-1.5 rounded-lg bg-slate-950 p-1">
          <button
            onClick={() => { setActiveTab("grid"); setFocusedVideo(null); }}
            className={`flex items-center gap-1.5 rounded-md px-3 py-1 text-xs font-semibold transition-all ${
              activeTab === "grid"
                ? "bg-slate-900 text-accent shadow-sm"
                : "text-slate-400 hover:text-slate-200"
            }`}
          >
            <Video className="h-3.5 w-3.5" /> Workspace Grid (2x2)
          </button>
          
          <button
            disabled={!feedsAvailable.overlay}
            onClick={() => { setActiveTab("pose"); setFocusedVideo(null); }}
            className={`flex items-center gap-1.5 rounded-md px-3 py-1 text-xs font-semibold transition-all ${
              !feedsAvailable.overlay ? "cursor-not-allowed opacity-40" : ""
            } ${
              activeTab === "pose"
                ? "bg-slate-900 text-accent shadow-sm"
                : "text-slate-400 hover:text-slate-200"
            }`}
          >
            <Activity className="h-3.5 w-3.5" /> Pose Tracker (1x2)
          </button>

          <button
            disabled={!feedsAvailable.simulation || !hasRobot}
            onClick={() => { setActiveTab("retarget"); setFocusedVideo(null); }}
            className={`flex items-center gap-1.5 rounded-md px-3 py-1 text-xs font-semibold transition-all ${
              (!feedsAvailable.simulation || !hasRobot) ? "cursor-not-allowed opacity-40" : ""
            } ${
              activeTab === "retarget"
                ? "bg-slate-900 text-accent shadow-sm"
                : "text-slate-400 hover:text-slate-200"
            }`}
          >
            <RefreshCcw className="h-3.5 w-3.5" /> Retarget Analyzer (1x2)
          </button>
        </div>

        {focusedVideo && (
          <button
            onClick={() => setFocusedVideo(null)}
            className="flex items-center gap-1 rounded-md border border-slate-800 bg-slate-900 px-2.5 py-1 text-xs font-medium text-slate-400 hover:text-slate-200"
          >
            <Minimize2 className="h-3.5 w-3.5" /> Exit Focus
          </button>
        )}
      </div>

      {/* Videos Display Grid */}
      <div className="relative overflow-hidden rounded-xl border border-slate-800 bg-slate-950 p-2">
        {/* Render Single focused video */}
        {focusedVideo ? (
          <div className="relative flex flex-col gap-1.5">
            <span className="text-[10px] font-bold tracking-wider text-slate-500 uppercase">
              {allVideos.find((v) => v.id === focusedVideo)?.label}
            </span>
            <video
              ref={focusedVideo === "original" ? originalRef : focusedVideo === "overlay" ? overlayRef : focusedVideo === "preview" ? previewRef : simulationRef}
              src={getUrl(focusedVideo === "simulation" ? "simulation" : focusedVideo === "overlay" ? "skeleton-overlay" : focusedVideo === "preview" ? "skeleton-preview" : "original")}
              className="w-full rounded-lg bg-slate-900 object-contain shadow-2xl"
              style={{ maxHeight: "400px" }}
              playsInline
              muted
              onTimeUpdate={focusedVideo === "original" ? handleVideoTimeUpdate : undefined}
              onSeeked={focusedVideo === "original" ? handleVideoSeeked : undefined}
            />
          </div>
        ) : (
          /* Normal Layout Grid */
          <div
            className={`grid gap-3 transition-all duration-300 ${
              activeTab === "grid" ? "grid-cols-2" : "grid-cols-2"
            }`}
          >
            {/* Viewport 1: Original */}
            {(activeTab === "grid" || activeTab === "pose") && (
              <div className="relative flex flex-col gap-1">
                <div className="flex items-center justify-between">
                  <span className="text-[10px] font-bold tracking-wider text-slate-400 uppercase">Original</span>
                  <button
                    onClick={() => setFocusedVideo("original")}
                    className="rounded p-0.5 text-slate-500 hover:bg-slate-900 hover:text-slate-300"
                  >
                    <Maximize2 className="h-3 w-3" />
                  </button>
                </div>
                <video
                  ref={originalRef}
                  src={getUrl("original")}
                  className="w-full rounded-lg border border-slate-900 bg-slate-900 object-contain"
                  style={{ maxHeight: activeTab === "grid" ? "170px" : "320px" }}
                  playsInline
                  muted
                  onTimeUpdate={handleVideoTimeUpdate}
                  onSeeked={handleVideoSeeked}
                />
              </div>
            )}

            {/* Viewport 2: Overlay */}
            {(activeTab === "grid" || activeTab === "pose" || activeTab === "retarget") &&
              feedsAvailable.overlay && (
                <div
                  className={`relative flex flex-col gap-1 ${
                    activeTab === "retarget" ? "order-1" : ""
                  }`}
                >
                  <div className="flex items-center justify-between">
                    <span className="text-[10px] font-bold tracking-wider text-slate-400 uppercase">Skeleton Overlay</span>
                    <button
                      onClick={() => setFocusedVideo("overlay")}
                      className="rounded p-0.5 text-slate-500 hover:bg-slate-900 hover:text-slate-300"
                    >
                      <Maximize2 className="h-3 w-3" />
                    </button>
                  </div>
                  <video
                    ref={overlayRef}
                    src={getUrl("skeleton-overlay")}
                    onError={() => handleVideoError("overlay")}
                    className="w-full rounded-lg border border-slate-900 bg-slate-900 object-contain"
                    style={{ maxHeight: activeTab === "grid" ? "170px" : "320px" }}
                    playsInline
                    muted
                  />
                </div>
              )}

            {/* Viewport 3: 3D Preview */}
            {activeTab === "grid" && feedsAvailable.preview && (
              <div className="relative flex flex-col gap-1">
                <div className="flex items-center justify-between">
                  <span className="text-[10px] font-bold tracking-wider text-slate-400 uppercase">Skeletal 3D</span>
                  <button
                    onClick={() => setFocusedVideo("preview")}
                    className="rounded p-0.5 text-slate-500 hover:bg-slate-900 hover:text-slate-300"
                  >
                    <Maximize2 className="h-3 w-3" />
                  </button>
                </div>
                <video
                  ref={previewRef}
                  src={getUrl("skeleton-preview")}
                  onError={() => handleVideoError("preview")}
                  className="w-full rounded-lg border border-slate-900 bg-slate-900 object-contain"
                  style={{ maxHeight: "170px" }}
                  playsInline
                  muted
                />
              </div>
            )}

            {/* Viewport 4: Sim */}
            {(activeTab === "grid" || activeTab === "retarget") &&
              feedsAvailable.simulation &&
              hasRobot && (
                <div className="relative flex flex-col gap-1 order-2">
                  <div className="flex items-center justify-between">
                    <span className="text-[10px] font-bold tracking-wider text-slate-400 uppercase">Robot Sim</span>
                    <button
                      onClick={() => setFocusedVideo("simulation")}
                      className="rounded p-0.5 text-slate-500 hover:bg-slate-900 hover:text-slate-300"
                    >
                      <Maximize2 className="h-3 w-3" />
                    </button>
                  </div>
                  <video
                    ref={simulationRef}
                    src={getUrl("simulation")}
                    onError={() => handleVideoError("simulation")}
                    className="w-full rounded-lg border border-slate-900 bg-slate-900 object-contain"
                    style={{ maxHeight: activeTab === "grid" ? "170px" : "320px" }}
                    playsInline
                    muted
                  />
                </div>
              )}
          </div>
        )}
      </div>

      {/* Synchronized media player controls panel */}
      <div className="flex items-center justify-between rounded-lg border border-slate-800 bg-slate-900/60 p-2.5 px-4">
        <div className="flex items-center gap-3">
          <button
            onClick={handlePlayToggle}
            className="flex h-8 w-8 items-center justify-center rounded-full bg-accent text-slate-950 shadow hover:bg-accent-hover transition-colors"
          >
            {isPlaying ? <Pause className="h-4 w-4 fill-slate-950" /> : <Play className="h-4 w-4 fill-slate-950 ml-0.5" />}
          </button>
          
          <button
            onClick={handleRestart}
            className="flex h-7 w-7 items-center justify-center rounded-full border border-slate-800 bg-slate-950 text-slate-400 hover:text-slate-200 transition-colors"
            title="Restart playback"
          >
            <RefreshCw className="h-3.5 w-3.5" />
          </button>
        </div>

        <span className="font-mono text-xs text-slate-400 select-none">
          {currentTime.toFixed(1)}s / {duration.toFixed(1)}s
        </span>
      </div>
    </div>
  );
}
