"use client";
import { useEffect, useState } from "react";

/** The skill count rolls up digit by digit. Reduced motion lands on the number at once. */
export function Odometer({ value }: { value: number }) {
  const [ready, setReady] = useState(false);
  useEffect(() => {
    const id = requestAnimationFrame(() => requestAnimationFrame(() => setReady(true)));
    return () => cancelAnimationFrame(id);
  }, []);
  const chars = value.toLocaleString("en-US").split("");
  return (
    <span className="hb-odo" aria-label={value.toLocaleString("en-US")} role="img">
      {chars.map((ch, i) =>
        ch === "," ? (
          <span key={i} className="hb-odo-comma" aria-hidden="true">,</span>
        ) : (
          <span key={i} className="hb-odo-digit" aria-hidden="true">
            <i style={{ transform: ready ? `translateY(-${Number(ch)}em)` : undefined, transitionDelay: `${i * 140}ms` }}>
              {[0, 1, 2, 3, 4, 5, 6, 7, 8, 9].map((n) => (
                <s key={n}>{n}</s>
              ))}
            </i>
          </span>
        ),
      )}
    </span>
  );
}
