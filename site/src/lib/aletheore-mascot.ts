export const MASCOT_POSE_COORDINATES = {
  neutral: { x: 0, y: 0 },
  typing: { x: 100 / 3, y: 0 },
  working: { x: (100 * 2) / 3, y: 0 },
  review: { x: 100, y: 0 },
  explaining: { x: 0, y: 100 / 3 },
  thinking: { x: 100 / 3, y: 100 / 3 },
  "jump-ready": { x: (100 * 2) / 3, y: 100 / 3 },
  "jump-air": { x: 100, y: 100 / 3 },
  landing: { x: 0, y: (100 * 2) / 3 },
  walking: { x: 100 / 3, y: (100 * 2) / 3 },
  waving: { x: (100 * 2) / 3, y: (100 * 2) / 3 },
  success: { x: 100, y: (100 * 2) / 3 },
  caution: { x: 0, y: 100 },
  reading: { x: 100 / 3, y: 100 },
  curious: { x: (100 * 2) / 3, y: 100 },
  resting: { x: 100, y: 100 },
} as const;

export type MascotPose = keyof typeof MASCOT_POSE_COORDINATES;
export type MascotMotion = "none" | "idle" | "typing" | "jump";
