"use client";

import React, { useRef, useEffect } from "react";

interface TrajectoryChartProps {
  trajectory: number[][];
  currentTime: number;
  duration: number;
  onScrub: (time: number) => void;
}

export default function TrajectoryChart({
  trajectory,
  currentTime,
  duration,
  onScrub,
}: TrajectoryChartProps) {
  const containerRef = useRef<HTMLDivElement>(null);
  const svgRef = useRef<SVGSVGElement>(null);

  if (!trajectory || trajectory.length === 0) {
    return (
      <div className="flex h-48 items-center justify-center rounded-lg border border-slate-800 bg-slate-950/50 text-slate-500 text-sm">
        No joint trajectory data available
      </div>
    );
  }

  const width = 500;
  const height = 220;
  const padding = { top: 15, right: 15, bottom: 25, left: 35 };
  const chartWidth = width - padding.left - padding.right;
  const chartHeight = height - padding.top - padding.bottom;

  // Find min/max values in trajectory
  let yMin = -Math.PI;
  let yMax = Math.PI;
  trajectory.forEach((pt) => {
    pt.forEach((val) => {
      if (val < yMin) yMin = val;
      if (val > yMax) yMax = val;
    });
  });
  const yRange = yMax - yMin;
  yMin -= yRange * 0.05;
  yMax += yRange * 0.05;

  const xMax = trajectory.length - 1;

  const getX = (index: number) => padding.left + (index / xMax) * chartWidth;
  const getY = (val: number) =>
    padding.top + chartHeight - ((val - yMin) / (yMax - yMin)) * chartHeight;

  const colors = [
    "#38bdf8", // Sky blue (Joint 0)
    "#f43f5e", // Rose (Joint 1)
    "#34d399", // Emerald (Joint 2)
    "#fbbf24", // Amber (Joint 3)
    "#a78bfa", // Purple (Joint 4)
    "#fb7185", // Pink (Joint 5)
    "#2dd4bf", // Teal (Joint 6)
  ];

  // Playhead position (X coordinate on chart)
  const playheadPct = duration > 0 ? currentTime / duration : 0;
  const playheadX = padding.left + playheadPct * chartWidth;

  const handleInteraction = (e: React.MouseEvent<SVGSVGElement, MouseEvent>) => {
    if (!svgRef.current || duration <= 0) return;
    const rect = svgRef.current.getBoundingClientRect();
    const clickX = e.clientX - rect.left - padding.left;
    const pct = Math.max(0, Math.min(1, clickX / (rect.width * (chartWidth / width))));
    onScrub(pct * duration);
  };

  const handleMouseMove = (e: React.MouseEvent<SVGSVGElement, MouseEvent>) => {
    if (e.buttons === 1) {
      handleInteraction(e);
    }
  };

  return (
    <div ref={containerRef} className="w-full flex flex-col gap-2">
      <div className="flex items-center justify-between">
        <h4 className="text-xs font-semibold tracking-wider text-slate-400 uppercase">
          Joint Trajectories (Actions)
        </h4>
        <span className="text-[10px] font-mono text-slate-500">
          Click / Drag chart to scrub timeline
        </span>
      </div>
      <div className="relative overflow-hidden rounded-lg border border-slate-800 bg-slate-950/80 p-2">
        <svg
          ref={svgRef}
          width="100%"
          height={height}
          viewBox={`0 0 ${width} ${height}`}
          className="cursor-ew-resize select-none"
          onMouseDown={handleInteraction}
          onMouseMove={handleMouseMove}
        >
          {/* Grid lines and ticks */}
          {Array.from({ length: 5 }).map((_, i) => {
            const val = yMin + (i / 4) * (yMax - yMin);
            const y = getY(val);
            return (
              <React.Fragment key={`y-${i}`}>
                <line
                  x1={padding.left}
                  y1={y}
                  x2={width - padding.right}
                  y2={y}
                  className="stroke-slate-800/80"
                  strokeDasharray="2 4"
                />
                <text
                  x={padding.left - 8}
                  y={y + 3}
                  className="fill-slate-500 font-mono text-[9px]"
                  textAnchor="end"
                >
                  {val.toFixed(1)}
                </text>
              </React.Fragment>
            );
          })}

          {Array.from({ length: 5 }).map((_, i) => {
            const pct = i / 4;
            const idx = Math.round(pct * xMax);
            const x = getX(idx);
            return (
              <React.Fragment key={`x-${i}`}>
                <line
                  x1={x}
                  y1={padding.top}
                  x2={x}
                  y2={height - padding.bottom}
                  className="stroke-slate-800/80"
                  strokeDasharray="2 4"
                />
                <text
                  x={x}
                  y={height - padding.bottom + 14}
                  className="fill-slate-500 font-mono text-[9px]"
                  textAnchor="middle"
                >
                  {(idx * 0.1).toFixed(1)}s
                </text>
              </React.Fragment>
            );
          })}

          {/* Path lines for 7 joints */}
          {Array.from({ length: 7 }).map((_, j) => {
            const pathPoints = trajectory.map((pt, i) => {
              const x = getX(i);
              const y = getY(pt[j] ?? 0);
              return `${i === 0 ? "M" : "L"} ${x} ${y}`;
            });
            return (
              <path
                key={`joint-${j}`}
                d={pathPoints.join(" ")}
                fill="none"
                stroke={colors[j]}
                strokeWidth="1.5"
                strokeLinecap="round"
                strokeLinejoin="round"
                className="opacity-85 hover:opacity-100 hover:stroke-[2px] transition-all duration-150"
              />
            );
          })}

          {/* Time scrubber vertical cursor playhead */}
          {duration > 0 && (
            <g>
              <line
                x1={playheadX}
                y1={padding.top}
                x2={playheadX}
                y2={height - padding.bottom}
                className="stroke-accent"
                strokeWidth="1.5"
              />
              <circle
                cx={playheadX}
                cy={padding.top}
                r="3"
                className="fill-accent stroke-slate-950"
                strokeWidth="1"
              />
            </g>
          )}
        </svg>

        {/* Legend */}
        <div className="mt-2 flex flex-wrap justify-center gap-x-3 gap-y-1 border-t border-slate-900 pt-2 text-[10px] font-medium text-slate-400">
          {colors.map((c, idx) => (
            <div key={`legend-${idx}`} className="flex items-center gap-1">
              <span className="h-1.5 w-3 rounded-sm" style={{ backgroundColor: c }} />
              <span>Joint {idx}</span>
            </div>
          ))}
        </div>
      </div>
    </div>
  );
}
