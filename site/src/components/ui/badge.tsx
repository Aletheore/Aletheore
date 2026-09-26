import * as React from "react";
import { cn } from "@/lib/utils";

export function Badge({ className, ...props }: React.HTMLAttributes<HTMLSpanElement>) {
  return (
    <span
      className={cn(
        "inline-block rounded-[3px] border border-stamp px-[7px] py-[2px] font-display text-[10.5px] text-stamp",
        className,
      )}
      {...props}
    />
  );
}
