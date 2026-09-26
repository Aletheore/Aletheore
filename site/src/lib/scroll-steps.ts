export function stepForProgress(progress: number, count: number): number {
  if (!Number.isFinite(progress) || count <= 1) return 0;
  const clamped = Math.min(1, Math.max(0, progress));
  return Math.min(count - 1, Math.floor(clamped * count));
}
