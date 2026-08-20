import React, { useEffect, useRef } from "react";

// Ambient signature background: a loose network of nodes with light pulses
// traveling along edges — a quiet visual echo of capcode's own mechanic
// (a prompt handed stage to stage through a chain of models). Nodes drift
// slowly; pulses fire on a random cadence between neighbors. Pink and
// canary alternate so the two brand accents both appear, sparingly.

export const AmbientBackground = () => {
  const canvasRef = useRef(null);

  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas) return;
    const ctx = canvas.getContext("2d");
    const reduceMotion = window.matchMedia?.("(prefers-reduced-motion: reduce)").matches;

    let width, height, dpr;
    const nodes = [];
    const NODE_COUNT = 16;
    const LINK_DIST = 220;

    const resize = () => {
      dpr = Math.min(window.devicePixelRatio || 1, 2);
      width = canvas.clientWidth;
      height = canvas.clientHeight;
      canvas.width = width * dpr;
      canvas.height = height * dpr;
      ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    };
    resize();
    window.addEventListener("resize", resize);

    for (let i = 0; i < NODE_COUNT; i++) {
      nodes.push({
        x: Math.random() * width,
        y: Math.random() * height,
        vx: (Math.random() - 0.5) * 0.12,
        vy: (Math.random() - 0.5) * 0.12,
      });
    }

    const pulses = []; // { a, b, t, color }
    const colors = ["#FF1F5E", "#F5E600"];

    const maybeSpawnPulse = () => {
      if (Math.random() > 0.02) return;
      const a = nodes[Math.floor(Math.random() * nodes.length)];
      let b = null, best = Infinity;
      for (const n of nodes) {
        if (n === a) continue;
        const d = Math.hypot(n.x - a.x, n.y - a.y);
        if (d < LINK_DIST && d < best) { best = d; b = n; }
      }
      if (b) pulses.push({ a, b, t: 0, color: colors[Math.floor(Math.random() * colors.length)] });
    };

    let raf;
    const draw = () => {
      ctx.clearRect(0, 0, width, height);

      for (const n of nodes) {
        n.x += n.vx; n.y += n.vy;
        if (n.x < 0 || n.x > width) n.vx *= -1;
        if (n.y < 0 || n.y > height) n.vy *= -1;
      }

      for (let i = 0; i < nodes.length; i++) {
        for (let j = i + 1; j < nodes.length; j++) {
          const d = Math.hypot(nodes[i].x - nodes[j].x, nodes[i].y - nodes[j].y);
          if (d < LINK_DIST) {
            ctx.strokeStyle = `rgba(147,150,159,${0.10 * (1 - d / LINK_DIST)})`;
            ctx.lineWidth = 1;
            ctx.beginPath();
            ctx.moveTo(nodes[i].x, nodes[i].y);
            ctx.lineTo(nodes[j].x, nodes[j].y);
            ctx.stroke();
          }
        }
      }

      for (const n of nodes) {
        ctx.fillStyle = "rgba(147,150,159,0.35)";
        ctx.beginPath();
        ctx.arc(n.x, n.y, 1.4, 0, Math.PI * 2);
        ctx.fill();
      }

      if (!reduceMotion) {
        maybeSpawnPulse();
        for (let i = pulses.length - 1; i >= 0; i--) {
          const p = pulses[i];
          p.t += 0.012;
          if (p.t >= 1) { pulses.splice(i, 1); continue; }
          const x = p.a.x + (p.b.x - p.a.x) * p.t;
          const y = p.a.y + (p.b.y - p.a.y) * p.t;
          const fade = Math.sin(p.t * Math.PI);
          ctx.fillStyle = p.color;
          ctx.globalAlpha = fade * 0.9;
          ctx.beginPath();
          ctx.arc(x, y, 2.2, 0, Math.PI * 2);
          ctx.fill();
          ctx.globalAlpha = fade * 0.25;
          ctx.beginPath();
          ctx.arc(x, y, 6, 0, Math.PI * 2);
          ctx.fill();
          ctx.globalAlpha = 1;
        }
        raf = requestAnimationFrame(draw);
      }
    };
    draw();

    return () => {
      window.removeEventListener("resize", resize);
      if (raf) cancelAnimationFrame(raf);
    };
  }, []);

  return (
    <canvas
      ref={canvasRef}
      aria-hidden="true"
      className="fixed inset-0 w-full h-full pointer-events-none"
      style={{ zIndex: 0 }}
    />
  );
};
