/**
 * The shared runtime settings served at /app.json (config/app.json), and the window-width check
 * they drive: useMinWidth follows the window as it is resized.
 */
import { useEffect, useState } from "react";
import { useQuery } from "@tanstack/react-query";

export interface SharedConfig {
  sidebar_min_width_px: number;
}

export function useAppConfig() {
  return useQuery<SharedConfig>({
    queryKey: ["app-config"],
    queryFn: async () => {
      const response = await fetch("/app.json");
      if (!response.ok) throw new Error(`GET /app.json failed: ${response.status}`);
      return response.json();
    },
    staleTime: Infinity,
  });
}

/** Whether the window is at least minWidthPx wide; null while the width is not yet known. */
export function useMinWidth(minWidthPx: number | undefined): boolean | null {
  const [matches, setMatches] = useState<boolean | null>(null);
  useEffect(() => {
    if (minWidthPx === undefined) return;
    const media = window.matchMedia(`(min-width: ${minWidthPx}px)`);
    const onChange = () => setMatches(media.matches);
    onChange();
    media.addEventListener("change", onChange);
    return () => media.removeEventListener("change", onChange);
  }, [minWidthPx]);
  return matches;
}
