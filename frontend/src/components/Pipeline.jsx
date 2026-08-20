import React from "react";
import { Cpu, Brain, Gauge, ArrowRight } from "lucide-react";

const STAGES = [
  { key: "gen", title: "BOT", model: "code-generator", icon: Cpu, color: "phosphor" },
  { key: "crit", title: "CRITIC", model: "file-review", icon: Brain, color: "neon_magenta" },
  { key: "rate", title: "RATER", model: "5-dim scorer", icon: Gauge, color: "neon_yellow" },
];

const colorMap = {
  phosphor: { txt: "text-phosphor", border: "border-phosphor/50", glow: "neon-text" },
  neon_magenta: { txt: "text-neon_magenta", border: "border-neon_magenta/60", glow: "neon-magenta" },
  neon_yellow: { txt: "text-neon_yellow", border: "border-neon_yellow/60", glow: "" },
};

export const Pipeline = ({ activeStage, busy }) => {
  return (
    <section className="panel p-4 sm:p-6" data-testid="pipeline">
      <div className="flex items-center gap-2 text-neon_cyan neon-cyan label-xs mb-4">
        <span>=== [ EVOLUTION PIPELINE ] ===</span>
      </div>
      <div className="grid grid-cols-1 md:grid-cols-[1fr_auto_1fr_auto_1fr] gap-3 items-stretch">
        {STAGES.map((s, idx) => {
          const c = colorMap[s.color];
          const active = busy && activeStage === s.key;
          return (
            <React.Fragment key={s.key}>
              <div
                data-testid={`pipeline-stage-${s.key}`}
                className={`border ${c.border} p-4 relative ${active ? "animate-flicker" : ""}`}
              >
                <div className="flex items-center justify-between mb-2">
                  <div className={`flex items-center gap-2 ${c.txt} ${c.glow}`}>
                    <s.icon size={16} />
                    <span className="font-bbs text-xl tracking-wider uppercase">{s.title}</span>
                  </div>
                  <span
                    className={`label-xs ${
                      active ? "text-neon_magenta animate-blink" : "text-phosphor3"
                    }`}
                  >
                    {active ? "[ACTIVE]" : busy ? "[QUEUED]" : "[IDLE]"}
                  </span>
                </div>
                <div className="text-[10px] sm:text-xs font-mono text-phosphor2 break-all">
                  {s.model}
                </div>
                <div className="mt-3 text-[10px] label-xs text-phosphor3">
                  {s.key === "gen" && "emits → real folder of runnable files"}
                  {s.key === "crit" && "emits → review // weakness clauses"}
                  {s.key === "rate" && "emits → 5-dim score vector"}
                </div>
              </div>
              {idx < STAGES.length - 1 && (
                <div className="hidden md:flex items-center justify-center text-neon_cyan neon-cyan">
                  <ArrowRight size={20} />
                </div>
              )}
            </React.Fragment>
          );
        })}
      </div>
    </section>
  );
};
