import React, { useEffect, useState } from "react";
import { Download, FileCode, GitBranch, Github, History } from "lucide-react";
import { toast } from "sonner";
import { API, api } from "@/lib/api";

const Bar = ({ label, value, max = 4, color = "phosphor" }) => {
  const pct = Math.max(0, Math.min(1, value / max));
  const filled = Math.round(pct * 18);
  const empty = 18 - filled;
  const cls =
    color === "magenta"
      ? "text-neon_magenta neon-magenta"
      : color === "cyan"
      ? "text-neon_cyan neon-cyan"
      : color === "yellow"
      ? "text-neon_yellow"
      : "text-phosphor neon-text";
  return (
    <div className="flex items-center justify-between gap-2 py-0.5 font-mono text-[11px] border-b border-phosphor/10">
      <span className="label-xs text-phosphor2 w-24">{label}</span>
      <span className={`${cls} flex-1`}>[{"|".repeat(filled)}{".".repeat(empty)}]</span>
      <span className={`${cls} tabular-nums w-10 text-right`}>{Number(value).toFixed(2)}</span>
    </div>
  );
};

const GenerationCard = ({ gen, chainId, billing }) => {
  const [openFile, setOpenFile] = useState(gen.files?.[0]?.path || null);
  const files = gen.files || [];
  const selected = files.find((f) => f.path === openFile) || files[0];
  const r = gen.reward || {};
  const _anonSid = (typeof window !== "undefined" && localStorage.getItem("capcode.session_id")) || "";
  const downloadHref = `${API}/chains/${chainId}/download/${gen.gen}?session_id=${encodeURIComponent(_anonSid)}`;

  return (
    <article
      className="panel p-4 sm:p-5 space-y-4"
      data-testid={`gen-card-${gen.gen}`}
      style={{ borderColor: "rgba(0,255,255,0.4)" }}
    >
      <header className="flex flex-col sm:flex-row sm:items-center sm:justify-between gap-2 border-b border-phosphor/30 pb-3">
        <div>
          <div className="label-xs text-neon_cyan neon-cyan">
            [ GEN-{String(gen.gen).padStart(2, "0")} ]
          </div>
          <h3 className="font-bbs text-2xl sm:text-3xl text-phosphor neon-text uppercase tracking-widest leading-none mt-1">
            {gen.name || `gen-${gen.gen}`}
          </h3>
          <p className="text-xs text-phosphor2 mt-1 italic">{gen.tagline}</p>
        </div>
        <a
          data-testid={`download-gen-${gen.gen}`}
          href={downloadHref}
          className="flex items-center gap-2 border border-phosphor bg-phosphor/10 text-phosphor px-3 py-2 hover:bg-phosphor hover:text-black transition-colors label-xs neon-text whitespace-nowrap"
        >
          <Download size={14} /> .ZIP
        </a>
      </header>

      <div className="grid grid-cols-1 sm:grid-cols-2 gap-2 text-[11px] font-mono">
        <div className="bg-black border-l-2 border-neon_magenta p-2">
          <div className="label-xs text-neon_magenta neon-magenta mb-1">MUTATION</div>
          <div className="text-phosphor">{gen.improvement_note || "—"}</div>
        </div>
        <div className="bg-black border-l-2 border-neon_yellow p-2">
          <div className="label-xs text-neon_yellow mb-1">PALETTE</div>
          <div className="flex items-center gap-2 text-phosphor">
            <span
              className="inline-block w-4 h-4 border border-phosphor/30"
              style={{ background: gen.accent_hex || "#7cffb2" }}
            />
            <span>{gen.accent_hex || "—"}</span>
            <span
              className="inline-block w-4 h-4 border border-phosphor/30 ml-2"
              style={{ background: gen.accent2_hex || "#ff79c6" }}
            />
            <span>{gen.accent2_hex || "—"}</span>
          </div>
        </div>
      </div>

      <div className="bg-black border-l-2 border-phosphor/60 p-2 text-[11px] sm:text-xs font-mono text-phosphor2 leading-relaxed">
        <span className="label-xs text-phosphor3 mr-1">PHILOSOPHY ::</span>
        {gen.philosophy}
      </div>

      {/* files */}
      {files.length > 0 && (
        <div>
          <div className="label-xs text-neon_yellow mb-2">
            [ CODE :: {files.length} files ]
          </div>
          <div className="grid grid-cols-1 lg:grid-cols-[200px_1fr] gap-2">
            <div className="bg-black border border-phosphor/30 p-1.5 font-mono text-[10px] max-h-56 overflow-y-auto">
              {files.map((f) => (
                <button
                  key={f.path}
                  data-testid={`gen-${gen.gen}-file-${f.path}`}
                  onClick={() => setOpenFile(f.path)}
                  className={`flex items-center gap-1 w-full text-left px-2 py-1 hover:bg-phosphor/10 ${
                    openFile === f.path
                      ? "bg-phosphor/15 text-phosphor neon-text"
                      : "text-phosphor2"
                  }`}
                >
                  <FileCode size={9} />
                  <span className="truncate">{f.path}</span>
                </button>
              ))}
            </div>
            <pre className="ascii bg-black border border-phosphor/30 p-2 text-[10px] sm:text-[11px] font-mono overflow-auto max-h-56 text-phosphor leading-relaxed">
              {selected ? (
                <>
                  <div className="label-xs text-neon_cyan neon-cyan mb-1 sticky top-0 bg-black border-b border-phosphor/15 pb-1">
                    {selected.path}
                  </div>
                  <code>{selected.content}</code>
                </>
              ) : (
                "—"
              )}
            </pre>
          </div>
        </div>
      )}

      {/* critic + scores */}
      <div className="grid grid-cols-1 lg:grid-cols-2 gap-3">
        <div className="bg-black border-l-2 border-neon_magenta p-2">
          <div className="label-xs text-neon_magenta neon-magenta mb-1">CRITIC</div>
          <p className="text-[11px] font-mono text-phosphor2 leading-relaxed">
            {gen.critic_notes}
          </p>
        </div>
        <div>
          <Bar label="HELP" value={r.helpfulness || 0} color="phosphor" />
          <Bar label="CORRECT" value={r.correctness || 0} color="cyan" />
          <Bar label="COHERE" value={r.coherence || 0} color="phosphor" />
          <Bar label="COMPLEX" value={r.complexity || 0} max={3} color="magenta" />
          <Bar label="VERBOSE" value={r.verbosity || 0} max={3} color="yellow" />
          <div className="flex justify-between mt-1 pt-1 border-t border-phosphor/30 font-mono">
            <span className="label-xs text-phosphor2">COMPOSITE</span>
            <span className="text-phosphor neon-text tabular-nums text-sm">
              {Number(gen.composite_score || 0).toFixed(3)}
            </span>
          </div>
          {gen.eval_weights && (
            <div className="mt-1 text-[10px] font-mono text-phosphor3" data-testid={`eval-weights-${gen.gen}`}>
              scored via: cov {Math.round(gen.eval_weights.coverage * 100)}% · rater {Math.round(gen.eval_weights.rater * 100)}% · novelty {Math.round(gen.eval_weights.novelty * 100)}%
            </div>
          )}
        </div>
      </div>

      {gen.refinement && (
        <div
          className="pt-2 border-t border-phosphor/10 font-mono text-[11px]"
          data-testid={`refinement-report-${gen.gen}`}
        >
          <div className="label-xs text-neon_yellow mb-1">TRANSPARENCY REPORT</div>
          {gen.refinement.ran ? (
            <p className="text-phosphor2 leading-relaxed">
              initial grade <span className="text-phosphor neon-text">{gen.refinement.initial_grade}/100</span>
              {gen.refinement.refined_grade !== null && (
                <> → refined grade <span className="text-phosphor neon-text">{gen.refinement.refined_grade}/100</span></>
              )}
              {" — "}
              {gen.refinement.water_tank_replenished ? (
                <span className="text-neon_cyan neon-cyan">
                  water tank replenished{gen.refinement.knowledge_module ? ` (${gen.refinement.knowledge_module.tag})` : ""}
                </span>
              ) : (
                <span className="text-phosphor3">no knowledge module saved this run</span>
              )}
            </p>
          ) : (
            <p className="text-phosphor2">
              grade <span className="text-phosphor neon-text">{gen.refinement.initial_grade}/100</span> — cleared the 80 bar on the first pass, delivered immediately.
            </p>
          )}
        </div>
      )}

      {billing && (
        <div
          className="pt-2 border-t border-phosphor/10 font-mono text-[11px]"
          data-testid={`billing-report-${gen.gen}`}
        >
          <div className="label-xs text-neon_yellow mb-1">BILLING SUMMARY</div>
          <p className="text-phosphor2 leading-relaxed">
            sub ${billing.billing_summary.sub_cost.toFixed(2)} · fund applied ${billing.billing_summary.fund_applied.toFixed(2)} ·
            {" "}user billed <span className={billing.billing_summary.user_billed > 0 ? "text-neon_magenta" : "text-phosphor2"}>${billing.billing_summary.user_billed.toFixed(2)}</span> ·
            {" "}profit ${billing.billing_summary.net_profit.toFixed(2)}
          </p>
          {billing.knowledge_audit.seval_found && (
            <p className="text-neon_cyan neon-cyan mt-1">
              SEVAL found — ${billing.knowledge_audit.credit_refunded.toFixed(2)} credited back
            </p>
          )}
        </div>
      )}

      {gen.exec?.root && (
        <div className="pt-2 border-t border-phosphor/10 font-mono text-[10px] text-phosphor3">
          <span className="label-xs">workspace ::</span>{" "}
          <code data-testid={`workspace-path-${gen.gen}`} className="text-phosphor2 break-all">
            {gen.exec.root}
          </code>
        </div>
      )}
    </article>
  );
};

export const ChainViewer = ({ chain, onVerify, onPush, streamBuf, advanceLog }) => {
  const [pushOpen, setPushOpen] = useState(false);
  const [repoName, setRepoName] = useState("");
  const [pushPrivate, setPushPrivate] = useState(true);
  const [pushing, setPushing] = useState(false);
  const [editDiffs, setEditDiffs] = useState([]);
  const [checkingEdits, setCheckingEdits] = useState(false);
  const uploadRef = React.useRef(null);

  useEffect(() => {
    if (chain?.status === "complete") {
      api.getEditDiffs(chain.id).then((r) => {
        // API returns full history (newest first) per file — collapse to
        // the latest hunk per file for display, same as the post-check merge below.
        const seen = new Set();
        const latestOnly = (r.diffs || []).filter((d) => {
          if (seen.has(d.file_path)) return false;
          seen.add(d.file_path);
          return true;
        });
        setEditDiffs(latestOnly);
      }).catch(() => {});
    }
  }, [chain?.id, chain?.status]);

  const runCheckEdits = async (file) => {
    setCheckingEdits(true);
    try {
      const res = file ? await api.checkEditsUpload(chain.id, file) : await api.checkEditsGit(chain.id);
      if (res.changed_files > 0) {
        toast.success(`found ${res.changed_files} corrected file(s) — learning from them`);
        setEditDiffs((prev) => {
          const byPath = new Map(prev.map((d) => [d.file_path, d]));
          for (const d of res.diffs) byPath.set(d.file_path, { ...d, detected_at: new Date().toISOString() });
          return Array.from(byPath.values());
        });
      } else {
        toast.info("no edits detected yet");
      }
    } catch (e) {
      toast.error(e?.response?.data?.detail || "couldn't check for edits");
    } finally {
      setCheckingEdits(false);
    }
  };

  if (!chain) {
    return (
      <section
        className="panel p-8 text-center"
        data-testid="chain-empty"
      >
        <div className="font-bbs text-3xl text-phosphor neon-text mb-3 uppercase tracking-widest">
          ░░ no build yet ░░
        </div>
        <p className="text-phosphor3 font-mono text-sm">
          type a target prompt above and press BUILD to run the Teacher → Artist → Product chain.
        </p>
      </section>
    );
  }

  const anonSid = (typeof window !== "undefined" && localStorage.getItem("capcode.session_id")) || "";
  const downloadAll = `${API}/chains/${chain.id}/download?session_id=${encodeURIComponent(anonSid)}`;
  const isComplete = chain.status === "complete";
  const isRunning = chain.status === "running";
  const buf = streamBuf || { teacher: "", artist: "" };
  const showStream = isRunning && (buf.teacher.length + buf.artist.length > 0);

  return (
    <section className="space-y-4 sm:space-y-6" data-testid="chain-viewer">
      <header
        className="panel p-4 sm:p-5 flex flex-col sm:flex-row sm:items-start sm:justify-between gap-3"
        style={{ borderColor: chain.status === "failed" ? "rgba(255,68,68,0.5)" : "rgba(255,0,255,0.5)" }}
      >
        <div className="min-w-0 flex-1">
          <div className="label-xs text-neon_magenta neon-magenta">
            [ CHAIN :: {chain.id.slice(0, 12)} ]
          </div>
          <div className="font-bbs text-2xl sm:text-3xl text-phosphor neon-text uppercase tracking-widest leading-none mt-1">
            {chain.status === "failed"
              ? "▲ build failed"
              : chain.verified
                ? "✓ verified build"
                : chain.status === "running"
                  ? "…building"
                  : "product build"}
          </div>
          {chain.target_prompt && (
            <p className="text-xs sm:text-sm text-phosphor2 mt-2 font-mono">
              <span className="text-phosphor3">target ::</span>{" "}
              <span data-testid="chain-target">{chain.target_prompt}</span>
            </p>
          )}
          {chain.status === "failed" && chain.error && (
            <p className="text-xs sm:text-sm mt-2 font-mono border-l-2 border-red-500 pl-3 py-1 bg-red-500/10"
               style={{ color: "#ff6b6b" }} data-testid="chain-error">
              <span className="text-red-400">error ::</span> {chain.error}
            </p>
          )}
          <p className="text-xs text-phosphor3 mt-1 font-mono">
            {new Date(chain.created_at).toLocaleString()}
            {chain.fallback_used && (
              <span className="text-amber_warn ml-2">[correction pass used]</span>
            )}
          </p>
        </div>
        <div className="flex flex-col gap-2 shrink-0">
          {isComplete && (
            <a
              data-testid="download-chain"
              href={downloadAll}
              className="flex items-center gap-2 border-2 border-phosphor bg-phosphor/10 text-phosphor px-4 py-2 hover:bg-phosphor hover:text-black transition-colors uppercase tracking-widest neon-text"
            >
              <Download size={16} />
              <span className="font-bbs text-base">download .zip</span>
            </a>
          )}
          {isComplete && !chain.verified && (
            <button
              data-testid="verify-chain"
              onClick={() => onVerify?.(chain.id)}
              className="flex items-center gap-2 border-2 border-neon_yellow bg-neon_yellow/10 text-neon_yellow px-4 py-2 hover:bg-neon_yellow hover:text-black transition-colors uppercase tracking-widest"
            >
              <span className="font-bbs text-base">✓ works — verify</span>
            </button>
          )}
          {isComplete && chain.verified && (
            <span className="border-2 border-neon_yellow text-neon_yellow px-3 py-2 label-xs text-center">
              ✓ verified
            </span>
          )}
          {isComplete && (
            <button
              data-testid="open-push"
              onClick={() => setPushOpen((v) => !v)}
              className="flex items-center gap-2 border border-neon_cyan/60 text-neon_cyan px-4 py-2 hover:bg-neon_cyan hover:text-black transition-colors label-xs"
            >
              <Github size={14} /> push to github
            </button>
          )}
          {isComplete && (
            <>
              <button
                data-testid="check-edits-btn"
                disabled={checkingEdits}
                onClick={() => (chain.github ? runCheckEdits() : uploadRef.current?.click())}
                className="flex items-center gap-2 border border-neon_yellow/60 text-neon_yellow px-4 py-2 hover:bg-neon_yellow hover:text-black transition-colors label-xs disabled:opacity-40"
                title="Detect what you changed after the build so future builds learn from it"
              >
                <History size={14} /> {checkingEdits ? "checking…" : chain.github ? "check for edits" : "upload edits"}
              </button>
              <input
                ref={uploadRef}
                data-testid="check-edits-upload-input"
                type="file"
                accept=".zip"
                className="hidden"
                onChange={(e) => { const f = e.target.files?.[0]; if (f) runCheckEdits(f); e.target.value = ""; }}
              />
            </>
          )}
        </div>
      </header>

      {pushOpen && isComplete && (
        <div className="panel p-4 space-y-3" data-testid="push-dialog"
             style={{ borderColor: "rgba(0,255,255,0.4)" }}>
          <div className="label-xs text-neon_cyan neon-cyan">[ PUSH :: NEW REPO ]</div>
          <div className="flex flex-col sm:flex-row gap-2">
            <input
              data-testid="push-repo-name"
              value={repoName}
              onChange={(e) => setRepoName(e.target.value)}
              placeholder="repo-name (e.g. my-cube)"
              className="flex-1 bg-black border border-phosphor/40 focus:border-neon_cyan text-phosphor px-3 py-2 font-mono text-sm outline-none"
            />
            <label className="flex items-center gap-1 text-xs font-mono text-phosphor2">
              <input
                data-testid="push-private"
                type="checkbox"
                checked={pushPrivate}
                onChange={(e) => setPushPrivate(e.target.checked)}
              />
              private
            </label>
            <button
              data-testid="push-confirm"
              disabled={pushing || !repoName.trim()}
              onClick={async () => {
                setPushing(true);
                try {
                  await onPush?.(chain.id, repoName.trim(), pushPrivate);
                  setPushOpen(false);
                } catch { /* toast handled upstream */ } finally { setPushing(false); }
              }}
              className="border-2 border-neon_cyan bg-neon_cyan/10 text-neon_cyan px-4 py-2 hover:bg-neon_cyan hover:text-black transition-colors label-xs disabled:opacity-40"
            >
              {pushing ? "pushing…" : "▶ push"}
            </button>
          </div>
          <p className="text-[10px] text-phosphor3 font-mono">
            requires your github username + a personal access token (scope:{" "}
            <code className="text-neon_cyan">repo</code>) saved under model settings.
          </p>
        </div>
      )}

      {editDiffs.length > 0 && (
        <div className="panel p-4 space-y-3" data-testid="edit-diffs-panel"
             style={{ borderColor: "rgba(245,230,0,0.35)" }}>
          <div className="label-xs text-neon_yellow">
            [ LEARNED CORRECTIONS :: {editDiffs.length} FILE{editDiffs.length > 1 ? "S" : ""} ]
          </div>
          <p className="text-[11px] text-phosphor3 font-mono">
            what you changed after this build got delivered — future Teacher briefs are steered away from repeating these.
          </p>
          {editDiffs.map((d) => (
            <details key={d.file_path} className="border-l-2 border-neon_yellow/40 pl-3" data-testid={`edit-diff-${d.file_path}`}>
              <summary className="text-xs font-mono text-neon_yellow cursor-pointer">
                {d.file_path}
                {d.seconds_since_build != null && (
                  <span className="text-phosphor3 ml-2">
                    ({Math.round(d.seconds_since_build / 60)}m after build)
                  </span>
                )}
              </summary>
              <pre className="text-[10px] font-mono text-phosphor2 whitespace-pre-wrap max-h-40 overflow-y-auto mt-1">
                {d.diff_hunk}
              </pre>
            </details>
          ))}
        </div>
      )}

      {advanceLog?.length > 0 && (
        <div className="panel p-3 space-y-2" data-testid="advance-log"
             style={{ borderColor: "rgba(0,255,255,0.35)" }}>
          <div className="label-xs text-neon_cyan neon-cyan mb-1">LIVE PROGRESS</div>
          {advanceLog.map((entry, i) => (
            <div key={i} className="text-[11px] font-mono text-phosphor2 leading-relaxed border-l-2 border-phosphor/30 pl-2"
                 data-testid={`advance-log-${entry.stage}`}>
              <span className="text-phosphor uppercase">{entry.stage}</span> :: {entry.report}
            </div>
          ))}
        </div>
      )}

      {showStream && (
        <div className="panel p-3 space-y-2" data-testid="stream-panel"
             style={{ borderColor: "rgba(0,255,255,0.35)" }}>
          {buf.teacher && (
            <div>
              <div className="label-xs text-neon_cyan neon-cyan mb-1">TEACHER (streaming)</div>
              <pre className="text-[11px] font-mono text-phosphor2 whitespace-pre-wrap max-h-32 overflow-y-auto"
                   data-testid="stream-teacher">{buf.teacher.slice(-1200)}</pre>
            </div>
          )}
          {buf.artist && (
            <div>
              <div className="label-xs text-neon_magenta neon-magenta mb-1">ARTIST (streaming)</div>
              <pre className="text-[11px] font-mono text-phosphor2 whitespace-pre-wrap max-h-40 overflow-y-auto"
                   data-testid="stream-artist">{buf.artist.slice(-1600)}</pre>
            </div>
          )}
        </div>
      )}

      {/* lineage breadcrumb */}
      <div
        className="panel p-3 flex items-center gap-2 overflow-x-auto"
        data-testid="chain-breadcrumb"
        style={{ borderColor: "rgba(57,255,20,0.25)" }}
      >
        <span className="label-xs text-phosphor3 shrink-0">HUMAN</span>
        <GitBranch size={12} className="text-neon_cyan shrink-0" />
        <span className="label-xs text-neon_cyan neon-cyan shrink-0 border border-neon_cyan/40 px-2 py-0.5">
          TEACHER
        </span>
        <GitBranch size={12} className="text-neon_magenta shrink-0" />
        <span className="label-xs text-neon_magenta neon-magenta shrink-0 border border-neon_magenta/40 px-2 py-0.5">
          ARTIST
        </span>
        <GitBranch size={12} className="text-neon_yellow shrink-0" />
        {chain.generations?.map((g) => (
          <span
            key={g.gen}
            className="label-xs text-neon_yellow shrink-0 border border-neon_yellow/40 px-2 py-0.5"
            data-testid={`breadcrumb-gen-${g.gen}`}
          >
            PRODUCT :: {g.name || "?"}
          </span>
        ))}
      </div>

      {chain.generations?.map((g) => (
        <GenerationCard key={g.gen} gen={g} chainId={chain.id} billing={chain.billing} />
      ))}
    </section>
  );
};
