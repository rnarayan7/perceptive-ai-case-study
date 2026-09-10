import type { Config } from "tailwindcss";

// Tokens from the Figma Foundations page (spec Appendix A).
const config: Config = {
  content: ["./app/**/*.{ts,tsx}", "./components/**/*.{ts,tsx}"],
  theme: {
    extend: {
      colors: {
        canvas: "#F5F6F8",
        surface: "#FFFFFF",
        sunken: "#EEF1F5",
        sidebar: "#0E1621",
        "sidebar-hover": "#1B2735",
        border: "#E4E8ED",
        "border-strong": "#CFD6DF",
        ink: "#111823",
        secondary: "#586173",
        tertiary: "#8B95A5",
        inverse: "#FFFFFF",
        "sidebar-text": "#AEB9C7",
        accent: "#2563EB",
        "accent-text": "#1D4ED8",
        "accent-soft": "#E7F0FE",
        "accent-soft2": "#EEF3FF",
        "conf-high": "#15803D",
        "conf-high-soft": "#DCFCE7",
        "conf-med": "#B45309",
        "conf-med-soft": "#FBEBCF",
        "conf-low": "#64748B",
        "conf-low-soft": "#EAEEF3",
        long: "#15803D",
        "long-soft": "#DCFCE7",
        short: "#DC2626",
        "short-soft": "#FCE4E4",
      },
      borderRadius: { sm: "6px", md: "8px", lg: "12px", pill: "999px" },
      fontFamily: {
        sans: ["var(--font-sans)", "system-ui", "sans-serif"],
        mono: ["var(--font-mono)", "ui-monospace", "monospace"],
      },
    },
  },
  plugins: [],
};
export default config;
