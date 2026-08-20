import React from "react";
import { ArrowLeft } from "lucide-react";

export const TerminalHero = ({ status, onBack }) => {
  return (
    <header className="flex items-center justify-between" data-testid="terminal-hero">
      <button
        onClick={onBack}
        className="flex items-center gap-2 text-text2 hover:text-text text-sm font-mono transition-colors"
        data-testid="back-to-landing"
      >
        <ArrowLeft size={14} /> capcode
      </button>
      <div className="flex items-center gap-2 text-xs font-mono text-text3">
        <span className={`w-1.5 h-1.5 rounded-full ${status ? "bg-canary" : "bg-text3"}`} />
        {status ? "online" : "connecting…"}
      </div>
    </header>
  );
};
