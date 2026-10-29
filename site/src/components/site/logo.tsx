import Image from "next/image";
import Link from "next/link";
import { cn } from "@/lib/utils";
import { AletheoreMascot } from "./aletheore-mascot";

export function Logo({ className, size = 22 }: { className?: string; size?: number }) {
  return (
    <Link href="/" className={cn("flex items-center gap-[9px] font-display text-base font-bold", className)}>
      <Image src="/logo-mark.png" alt="" width={size} height={size} className="rounded-[4px]" priority />
      Aletheore
      <AletheoreMascot pose="neutral" motion="idle" size={42} />
    </Link>
  );
}
