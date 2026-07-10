"use client";

import { MousePointer2, TrendingUp, Minus, Type, Ruler, Eraser, Trash2 } from "lucide-react";
import { cn } from "@/lib/utils";
import type { Tool } from "@/app/components/drawings";

const TOOLS: { tool: Tool; icon: typeof MousePointer2; label: string }[] = [
  { tool: "cross", icon: MousePointer2, label: "Cursor / crosshair" },
  { tool: "trendline", icon: TrendingUp, label: "Trend line — click two points" },
  { tool: "hline", icon: Minus, label: "Horizontal line — click a price" },
  { tool: "text", icon: Type, label: "Text — click to place" },
  { tool: "measure", icon: Ruler, label: "Measure — click two points" },
  { tool: "eraser", icon: Eraser, label: "Erase — click a drawing" },
];

export function ChartToolbar({ tool, onTool, onClear }: { tool: Tool; onTool: (t: Tool) => void; onClear: () => void }) {
  return (
    <div className="flex w-10 flex-col items-center gap-1 border-r border-border bg-card py-2">
      {TOOLS.map(({ tool: t, icon: Icon, label }) => (
        <button
          key={t}
          type="button"
          title={label}
          aria-label={label}
          aria-pressed={tool === t}
          onClick={() => onTool(t)}
          className={cn(
            "flex h-8 w-8 items-center justify-center rounded transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring",
            tool === t ? "bg-primary text-primary-foreground" : "text-muted-foreground hover:bg-accent hover:text-foreground",
          )}
        >
          <Icon className="h-4 w-4" />
        </button>
      ))}
      <div className="my-1 h-px w-5 bg-border" />
      <button
        type="button"
        title="Clear all drawings"
        aria-label="Clear all drawings"
        onClick={onClear}
        className="flex h-8 w-8 items-center justify-center rounded text-muted-foreground transition-colors hover:bg-destructive/20 hover:text-destructive focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
      >
        <Trash2 className="h-4 w-4" />
      </button>
    </div>
  );
}
