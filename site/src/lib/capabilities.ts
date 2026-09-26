type CanvasLike = { getContext(kind: string): unknown };

function defaultCreate(): CanvasLike | null {
  if (typeof document === "undefined") return null;
  return document.createElement("canvas");
}

export function hasWebGL(create: () => CanvasLike | null = defaultCreate): boolean {
  try {
    const canvas = create();
    if (!canvas) return false;
    return Boolean(canvas.getContext("webgl2") || canvas.getContext("webgl"));
  } catch {
    return false;
  }
}
