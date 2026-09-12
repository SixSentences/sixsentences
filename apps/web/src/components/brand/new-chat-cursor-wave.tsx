"use client";

import { useTheme } from "next-themes";

import CursorWave from "@/components/brand/cursor-wave";

const LIGHT_WAVE_COLORS = [
  "#0c1d19",
  "#33544c",
  "#5a897d",
  "#8db3a7",
  "#bfd4cc",
] as const;

const DARK_WAVE_COLORS = [
  "#f4f1ea",
  "#9fc0b7",
  "#86aca1",
  "#5a897d",
  "#33544c",
] as const;

type NewChatCursorWaveProps = {
  mode?: "light" | "dark";
};

/** Sparse, theme-aware cursor field used only before the first chat starts. */
export default function NewChatCursorWave({ mode }: NewChatCursorWaveProps = {}) {
  const { resolvedTheme } = useTheme();
  const isDark = mode ? mode === "dark" : resolvedTheme === "dark";

  return (
    <CursorWave
      backgroundColor="transparent"
      colors={isDark ? DARK_WAVE_COLORS : LIGHT_WAVE_COLORS}
      cellSize={40}
      influenceRadiusVmin={22}
      attackTime={0.5}
      releaseTime={0.6}
      idleScale={0.09}
      minPeakScale={1}
      maxPeakScale={3}
      burstSpeed={1200}
      burstThickness={180}
      opacity={isDark ? 0.95 : 0.84}
      className="absolute inset-0 z-0"
      style={{ position: "absolute" }}
    />
  );
}
