import React, { useState } from "react";
import { Terminal, Zap } from "lucide-react";

const SAMPLES = [
  "a kanban board with drag-and-drop cards",
  "a notes app with tags",
  "a habit tracker with streaks",
  "a chat app with rooms",
  "a todo list",
];

export const PromptConsole = ({ onSubmit, busy, parentId, onClearParent }) => {
  const [value, setValue] = useState("");

  const submit = () => {
    if (!value.trim() || busy) return;
    onSubmit(value.trim());
    setValue("");
  };

  return (
    <section
      className="panel p-4 sm:p-6 relative"
      data-testid="prompt-console"
      style={{ borderColor: "rgba(57,255,20,0.5)" }}
    >
      <div className="flex items-center justify-between mb-3">
        <div className="flex items-center gap-2 text-neon_cyan neon-cyan label-xs">
          <Terminal size={14} />
          <span>=== [ PROMPT CONSOLE ] ===</span>
        </div>
        {parentId && (
          <button
            data-testid="parent-chip"
            onClick={onClearParent}
            className="text-[10px] label-xs text-amber_warn border border-amber_warn/50 px-2 py-1 hover:bg-amber_warn hover:text-black transition-colors"
          >
            FORK FROM :: {parentId.slice(0, 8)} <span data-testid="clear-parent-btn">✕</span>
          </button>
        )}
      </div>

      <div className="flex items-start gap-2 font-mono">
        <span className="text-neon_magenta neon-magenta select-none pt-2 text-sm sm:text-base">
          system@bbs:~$
        </span>
        <textarea
          data-testid="prompt-input"
          value={value}
          onChange={(e) => setValue(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter" && (e.metaKey || e.ctrlKey)) submit();
          }}
          placeholder="describe an app — e.g. 'a habit tracker that pays me when i succeed'"
          className="flex-1 bg-black border border-phosphor/40 text-phosphor p-3 font-mono text-sm sm:text-base resize-none placeholder:text-phosphor3"
          rows={3}
        />
        <span className="text-phosphor animate-blink pt-2 text-lg select-none">█</span>
      </div>

      <div className="flex flex-wrap items-center gap-2 mt-3">
        <span className="text-phosphor3 label-xs">try:</span>
        {SAMPLES.map((s, i) => (
          <button
            key={i}
            data-testid={`sample-prompt-${i}`}
            onClick={() => setValue(s)}
            className="text-[10px] label-xs text-neon_cyan border border-neon_cyan/30 px-2 py-1 hover:bg-neon_cyan hover:text-black transition-colors"
          >
            {s.split(" ").slice(0, 4).join(" ")}…
          </button>
        ))}
      </div>

      <div className="flex justify-end mt-4">
        <button
          data-testid="run-prompt-btn"
          onClick={submit}
          disabled={busy || !value.trim()}
          className="flex items-center gap-2 bg-transparent border border-phosphor text-phosphor hover:bg-phosphor hover:text-black uppercase tracking-widest px-6 py-2 text-sm transition-colors disabled:opacity-30 disabled:cursor-not-allowed neon-text"
        >
          <Zap size={14} />
          {busy ? "FORGING…" : "EXECUTE >>"}
        </button>
      </div>
    </section>
  );
};
