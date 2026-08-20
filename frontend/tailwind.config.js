/** @type {import('tailwindcss').Config} */
module.exports = {
  darkMode: ["class"],
  content: ["./src/**/*.{js,jsx,ts,tsx}", "./public/index.html"],
  theme: {
    extend: {
      fontFamily: {
        display: ["'Inter'", "ui-sans-serif", "sans-serif"],
        mono: ["'IBM Plex Mono'", "ui-monospace", "monospace"],
      },
      colors: {
        background: "hsl(var(--background))",
        foreground: "hsl(var(--foreground))",
        card: { DEFAULT: "hsl(var(--card))", foreground: "hsl(var(--card-foreground))" },
        popover: { DEFAULT: "hsl(var(--popover))", foreground: "hsl(var(--popover-foreground))" },
        primary: { DEFAULT: "hsl(var(--primary))", foreground: "hsl(var(--primary-foreground))" },
        secondary: { DEFAULT: "hsl(var(--secondary))", foreground: "hsl(var(--secondary-foreground))" },
        muted: { DEFAULT: "hsl(var(--muted))", foreground: "hsl(var(--muted-foreground))" },
        accent: { DEFAULT: "hsl(var(--accent))", foreground: "hsl(var(--accent-foreground))" },
        destructive: { DEFAULT: "hsl(var(--destructive))", foreground: "hsl(var(--destructive-foreground))" },
        border: "hsl(var(--border))",
        input: "hsl(var(--input))",
        ring: "hsl(var(--ring))",
        /* brand */
        ink: "#0A0A0C",        /* page background — near black */
        slate: "#15171D",      /* panel background — dark slate */
        slate2: "#1D202A",     /* raised/hover slate */
        line: "#2A2E38",       /* neutral border */
        text: "#EDEDF0",       /* primary text */
        text2: "#93969F",      /* muted text */
        text3: "#54575F",      /* faint text */
        pink: "#FF1F5E",       /* primary accent — neon pink/red */
        pinkDim: "#7A0F30",
        canary: "#F5E600",     /* secondary accent — neon canary yellow */
        canaryDim: "#7A7000",
        warn: "#FFB000",
        /* legacy aliases kept so existing components resolve without a full rewrite */
        phosphor: "#FF1F5E",
        phosphor2: "#93969F",
        phosphor3: "#54575F",
        neon_magenta: "#F5E600",
        neon_cyan: "#93969F",
        neon_yellow: "#F5E600",
        amber_warn: "#FFB000",
      },
      borderRadius: {
        lg: "10px",
        md: "8px",
        sm: "6px",
      },
      keyframes: {
        blink: { "50%": { opacity: "0" } },
      },
      animation: {
        blink: "blink 1s steps(2) infinite",
      },
    },
  },
  plugins: [require("tailwindcss-animate")],
};
