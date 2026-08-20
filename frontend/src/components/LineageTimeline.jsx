import React from "react";
import { Network } from "lucide-react";

export const LineageTimeline = ({ nodes, onSelect, selectedId }) => {
  return (
    <section
      className="panel p-4 sm:p-5"
      data-testid="lineage-timeline"
      style={{ borderColor: "rgba(255,0,255,0.5)" }}
    >
      <div className="flex items-center gap-2 text-neon_magenta neon-magenta label-xs mb-3">
        <Network size={14} />
        <span>=== [ LINEAGE // EVOLUTION TREE ] ===</span>
      </div>
      {(!nodes || nodes.length === 0) && (
        <div className="text-phosphor3 font-mono text-xs py-6 text-center">
          ░ no genealogy yet ░
        </div>
      )}
      <div className="flex gap-2 overflow-x-auto pb-2">
        {nodes?.map((n) => {
          const score = Number(n.composite_score || 0);
          const intensity = Math.max(0.2, Math.min(1, score / 4));
          return (
            <button
              key={n.id}
              data-testid={`lineage-node-${n.id.slice(0, 8)}`}
              onClick={() => onSelect(n.id)}
              className={`shrink-0 border px-2 py-2 font-mono text-[10px] hover:bg-neon_cyan hover:text-black transition-colors ${
                selectedId === n.id
                  ? "bg-neon_cyan text-black border-neon_cyan"
                  : "text-neon_cyan border-neon_cyan/60"
              }`}
              style={{
                boxShadow:
                  selectedId === n.id
                    ? "none"
                    : `0 0 ${intensity * 12}px rgba(0,255,255,${intensity * 0.5})`,
              }}
              title={n.app?.name || n.bot?.name || n.id}
            >
              <div className="label-xs">[GEN-{String(n.generation).padStart(3, "0")}]</div>
              <div className="text-[10px] mt-1 max-w-[120px] truncate">
                {n.app?.name || n.bot?.name || n.id.slice(0, 8)}
              </div>
              <div className="text-[10px] mt-1 tabular-nums">
                ★ {score.toFixed(2)}
              </div>
            </button>
          );
        })}
      </div>
    </section>
  );
};
