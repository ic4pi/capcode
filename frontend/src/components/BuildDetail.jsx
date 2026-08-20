import React, { useMemo, useState } from "react";
import { ThumbsUp, ThumbsDown, GitBranch, Download, FileCode, Folder } from "lucide-react";
import { API } from "@/lib/api";

const Bar = ({ label, value, max = 4, color = "phosphor" }) => {
  const pct = Math.max(0, Math.min(1, value / max));
  const filled = Math.round(pct * 20);
  const empty = 20 - filled;
  const colorClass =
    color === "magenta"
      ? "text-neon_magenta neon-magenta"
      : color === "cyan"
      ? "text-neon_cyan neon-cyan"
      : color === "yellow"
      ? "text-neon_yellow"
      : "text-phosphor neon-text";
  return (
    <div className="flex items-center justify-between gap-3 py-1 font-mono text-xs sm:text-sm border-b border-phosphor/15">
      <span className="label-xs text-phosphor2 w-24">{label}</span>
      <span className={`${colorClass} flex-1 truncate`}>
        [{"|".repeat(filled)}{".".repeat(empty)}]
      </span>
      <span className={`${colorClass} tabular-nums w-12 text-right`}>{value.toFixed(2)}</span>
    </div>
  );
};

const FileTree = ({ files, selected, onSelect }) => {
  // group by top-level directory
  const groups = useMemo(() => {
    const out = {};
    for (const f of files || []) {
      const parts = f.path.split("/");
      const top = parts.length > 1 ? parts[0] : "/";
      out[top] = out[top] || [];
      out[top].push(f);
    }
    return out;
  }, [files]);

  return (
    <div
      data-testid="file-tree"
      className="bg-black border border-phosphor/30 p-2 font-mono text-xs max-h-72 overflow-y-auto"
    >
      {Object.entries(groups).map(([dir, items]) => (
        <div key={dir} className="mb-2">
          <div className="flex items-center gap-1 text-neon_cyan neon-cyan label-xs mb-1">
            <Folder size={10} /> {dir}
          </div>
          <ul>
            {items.map((f) => {
              const tail = f.path.split("/").slice(1).join("/") || f.path;
              const active = selected === f.path;
              return (
                <li key={f.path}>
                  <button
                    data-testid={`file-${f.path}`}
                    onClick={() => onSelect(f.path)}
                    className={`flex items-center gap-1 w-full text-left px-2 py-1 hover:bg-phosphor/10 ${
                      active ? "bg-phosphor/15 text-phosphor neon-text" : "text-phosphor2"
                    }`}
                  >
                    <FileCode size={10} />
                    <span className="truncate">{tail}</span>
                    <span className="ml-auto text-phosphor3 text-[10px]">
                      {f.content.split("\n").length}L
                    </span>
                  </button>
                </li>
              );
            })}
          </ul>
        </div>
      ))}
    </div>
  );
};

const CodePane = ({ file }) => {
  if (!file) {
    return (
      <div className="bg-black border border-phosphor/30 p-4 font-mono text-xs text-phosphor3 max-h-72 flex items-center justify-center">
        select a file from the tree →
      </div>
    );
  }
  return (
    <pre
      data-testid="code-pane"
      className="ascii bg-black border border-phosphor/30 p-3 text-[11px] sm:text-xs font-mono overflow-auto max-h-72 text-phosphor leading-relaxed"
    >
      <div className="label-xs text-neon_cyan neon-cyan mb-2 sticky top-0 bg-black pb-1 border-b border-phosphor/20">
        {file.path}
      </div>
      <code>{file.content}</code>
    </pre>
  );
};

export const BuildDetail = ({ build, onFeedback, onFork }) => {
  const [selectedPath, setSelectedPath] = useState(null);

  if (!build) {
    return (
      <section
        className="panel p-6 text-center text-phosphor3 font-mono"
        data-testid="build-detail-empty"
      >
        <div className="font-bbs text-2xl text-phosphor neon-text mb-2 uppercase">
          ░░ awaiting transmission ░░
        </div>
        <div className="text-sm">
          submit a prompt above to forge a new code-builder bot.
        </div>
      </section>
    );
  }

  const r = build.reward || {};
  const files = build.files || [];
  const selectedFile = files.find((f) => f.path === selectedPath) || files[0];
  const downloadHref = `${API}/builds/${build.id}/download`;
  const bot = build.bot || {};
  const appInfo = build.app || {};

  return (
    <section className="panel p-4 sm:p-6 space-y-5" data-testid="build-detail">
      {/* header */}
      <div className="flex flex-col sm:flex-row sm:items-center sm:justify-between gap-2 border-b border-phosphor/30 pb-3">
        <div>
          <div className="label-xs text-neon_cyan neon-cyan">
            [ BUILD :: {build.id.slice(0, 12)} // GEN-{String(build.generation).padStart(3, "0")} ]
          </div>
          <div className="font-bbs text-3xl sm:text-4xl text-phosphor neon-text uppercase tracking-widest leading-none mt-1">
            {appInfo.name || "untitled.app"}
          </div>
          <div className="text-xs sm:text-sm text-phosphor2 mt-1">
            bot :: <span className="text-neon_magenta neon-magenta">{bot.name || "—"}</span>
            {" "}// domain :: <span className="text-neon_cyan neon-cyan">{bot.domain || "—"}</span>
            {" "}// dna :: <span className="text-amber_warn">{bot.dna_signature || "—"}</span>
            {bot.inherited_from && (
              <> // inherits :: <span className="text-neon_cyan">{String(bot.inherited_from).slice(0, 8)}</span></>
            )}
          </div>
        </div>
        <div className="flex items-center gap-2 flex-wrap">
          <a
            data-testid="download-zip"
            href={downloadHref}
            className="flex items-center gap-2 border border-phosphor bg-phosphor/10 text-phosphor px-3 py-2 hover:bg-phosphor hover:text-black transition-colors label-xs neon-text"
          >
            <Download size={14} /> DOWNLOAD .ZIP
          </a>
          <button
            data-testid="feedback-up"
            onClick={() => onFeedback?.(build.id, 1)}
            className={`border px-3 py-2 transition-colors ${
              build.user_vote === 1
                ? "bg-phosphor text-black border-phosphor"
                : "border-phosphor/50 text-phosphor hover:bg-phosphor hover:text-black"
            }`}
            title="boost into future gene pool"
          >
            <ThumbsUp size={14} />
          </button>
          <button
            data-testid="feedback-down"
            onClick={() => onFeedback?.(build.id, -1)}
            className={`border px-3 py-2 transition-colors ${
              build.user_vote === -1
                ? "bg-destructive text-black border-destructive"
                : "border-destructive/50 text-destructive hover:bg-destructive hover:text-black"
            }`}
            title="suppress this lineage"
          >
            <ThumbsDown size={14} />
          </button>
          <button
            data-testid="fork-build"
            onClick={() => onFork?.(build.id)}
            className="flex items-center gap-1 border border-neon_cyan/60 text-neon_cyan px-3 py-2 hover:bg-neon_cyan hover:text-black transition-colors label-xs"
          >
            <GitBranch size={14} /> FORK
          </button>
        </div>
      </div>

      {/* prompt */}
      <div className="bg-black border border-phosphor/30 p-3 font-mono text-xs sm:text-sm">
        <span className="text-neon_magenta neon-magenta">user@bbs:~$ </span>
        <span className="text-phosphor">{build.user_prompt}</span>
      </div>

      {/* tagline + endpoint summary */}
      <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
        <div className="bg-black border-l-2 border-neon_cyan p-3 font-mono text-xs sm:text-sm">
          <div className="label-xs text-neon_cyan neon-cyan mb-1">[ TAGLINE ]</div>
          <div className="text-phosphor">{appInfo.tagline || "—"}</div>
          <div className="label-xs text-neon_cyan neon-cyan mt-3 mb-1">[ STACK ]</div>
          <div className="text-phosphor2">{(appInfo.stack || []).join(" + ")}</div>
        </div>
        <div className="bg-black border-l-2 border-amber_warn p-3 font-mono text-xs sm:text-sm">
          <div className="label-xs text-amber_warn mb-1">[ ENDPOINTS ]</div>
          <div className="space-y-0.5 max-h-32 overflow-y-auto">
            {(appInfo.endpoints || []).map((ep, i) => (
              <div key={i} className="flex gap-2">
                <span className="text-neon_magenta neon-magenta w-14 shrink-0">{ep.method}</span>
                <span className="text-phosphor truncate">{ep.path}</span>
              </div>
            ))}
          </div>
        </div>
      </div>

      {/* files: tree + code */}
      <div>
        <div className="label-xs text-neon_yellow mb-2">
          === [ GENERATED CODE :: {files.length} files ] ===
        </div>
        <div className="grid grid-cols-1 lg:grid-cols-[260px_1fr] gap-3">
          <FileTree files={files} selected={selectedFile?.path} onSelect={setSelectedPath} />
          <CodePane file={selectedFile} />
        </div>
      </div>

      {/* rater scorecard */}
      <div data-testid="rater-scorecard">
        <div className="label-xs text-neon_yellow mb-2">
          === [ RATER :: SCORECARD ] ===
        </div>
        <Bar label="HELPFULNESS" value={r.helpfulness || 0} color="phosphor" />
        <Bar label="CORRECTNESS" value={r.correctness || 0} color="cyan" />
        <Bar label="COHERENCE" value={r.coherence || 0} color="phosphor" />
        <Bar label="COMPLEXITY" value={r.complexity || 0} max={3} color="magenta" />
        <Bar label="VERBOSITY" value={r.verbosity || 0} max={3} color="yellow" />
        <div className="flex justify-between mt-3 pt-2 border-t border-phosphor/30 font-mono">
          <span className="label-xs text-phosphor2">COMPOSITE</span>
          <span
            data-testid="composite-score"
            className="text-phosphor neon-text text-lg tabular-nums"
          >
            {build.composite_score?.toFixed?.(3) ?? "0.000"}
          </span>
        </div>
      </div>

      {/* critic */}
      <div>
        <div className="label-xs text-neon_magenta neon-magenta mb-2">
          === [ CRITIC :: NOTES ] ===
        </div>
        <div className="bg-black border-l-2 border-neon_magenta p-3 font-mono text-xs sm:text-sm text-phosphor2 leading-relaxed">
          {build.critic_notes}
        </div>
      </div>
    </section>
  );
};
