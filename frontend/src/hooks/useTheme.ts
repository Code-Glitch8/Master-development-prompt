import { useCallback, useEffect, useState } from "react";

export type Theme = "dark" | "light";

function read(): Theme {
  try {
    const v = localStorage.getItem("pp-theme");
    if (v === "light" || v === "dark") return v;
  } catch { /* storage unavailable */ }
  return "dark";
}

export function useTheme() {
  const [theme, setTheme] = useState<Theme>(read);
  useEffect(() => {
    document.documentElement.setAttribute("data-theme", theme);
    try { localStorage.setItem("pp-theme", theme); } catch { /* ignore */ }
  }, [theme]);
  const toggle = useCallback(() => setTheme((t) => (t === "dark" ? "light" : "dark")), []);
  return { theme, setTheme, toggle };
}
