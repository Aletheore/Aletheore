import type { CSSProperties } from "react";
import {
  MASCOT_POSE_COORDINATES,
  type MascotMotion,
  type MascotPose,
} from "@/lib/aletheore-mascot";

type AletheoreMascotProps = {
  pose?: MascotPose;
  motion?: MascotMotion;
  size?: number;
  label?: string;
  className?: string;
};

export function AletheoreMascot({
  pose = "neutral",
  motion = "none",
  size = 64,
  label,
  className = "",
}: AletheoreMascotProps) {
  const { x, y } = MASCOT_POSE_COORDINATES[pose];
  const artStyle: CSSProperties = { backgroundPosition: `${x}% ${y}%` };

  return (
    <span
      className={`aletheore-mascot ${className}`.trim()}
      data-pose={pose}
      data-motion={motion}
      role={label ? "img" : undefined}
      aria-label={label}
      aria-hidden={label ? undefined : true}
      style={{ width: size, height: size }}
    >
      <span className="aletheore-mascot__art" style={artStyle} />
    </span>
  );
}
