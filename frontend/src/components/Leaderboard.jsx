import React from "react";
import { Trophy } from "lucide-react";

export const Leaderboard = ({ items, onSelect, selectedId }) => {
  return (
    <section
      className="panel p-4 sm:p-5 panel-cyan"
      data-testid="leaderboard"
      style={{ borderColor: "rgba(0,255,255,0.4)" }}
    >
      <div className="flex items-center gap-2 text-neon_cyan neon-cyan label-xs mb-3">
        <Trophy size={14} />
        <span>=== [ TOP-RATED // GENE POOL ] ===</span>
      </div>
      {(!items || items.length === 0) && (
        <div className="text-phosphor3 font-mono text-xs py-6 text-center">
          ░ no rated builds yet ░
        </div>
      )}
      <ul className="divide-y divide-phosphor/15">
        {items?.map((b, i) => (
          <li key={b.id}>
            <button
              data-testid={`leaderboard-row-${i}`}
              onClick={() => onSelect(b.id)}
              className={`w-full text-left flex items-center gap-3 py-2 px-1 font-mono text-xs sm:text-sm hover:bg-phosphor/5 transition-colors ${
                selectedId === b.id ? "bg-phosphor/10" : ""
              }`}
            >
              <span className="text-neon_yellow w-6 tabular-nums">#{i + 1}</span>
              <span className="text-phosphor flex-1 truncate">
                {b.app?.name || b.bot?.name || b.id.slice(0, 12)}
              </span>
              <span className="text-phosphor3 hidden sm:inline">
                GEN-{String(b.generation).padStart(3, "0")}
              </span>
              <span className="text-neon_magenta neon-magenta tabular-nums w-14 text-right">
                {Number(b.composite_score || 0).toFixed(2)}
              </span>
            </button>
          </li>
        ))}
      </ul>
    </section>
  );
};
