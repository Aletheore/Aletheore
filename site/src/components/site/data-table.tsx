import { cn } from "@/lib/utils";

export type Column = { key: string; label: string; align?: "left" | "right" };

/** A table that scrolls sideways inside its own box and never widens the page. */
export function DataTable({ columns, rows, caption, highlightFirstCol = true, oursKey }: {
  columns: Column[];
  rows: Record<string, string>[];
  caption: string;
  highlightFirstCol?: boolean;
  /** Rows whose first cell starts with this text are shown in the accent colour. */
  oursKey?: string;
}) {
  return (
    <div className="overflow-x-auto border border-line bg-raised" role="region" aria-label={caption} tabIndex={0}>
      <table className="w-full border-collapse text-left text-[12.5px]">
        <caption className="sr-only">{caption}</caption>
        <thead>
          <tr className="border-b border-line bg-paper">
            {columns.map((c) => (
              <th key={c.key} scope="col" className={cn("whitespace-nowrap px-3.5 py-2.5 font-display text-[11.5px] font-semibold text-muted", c.align === "right" && "text-right")}>{c.label}</th>
            ))}
          </tr>
        </thead>
        <tbody>
          {rows.map((row, i) => {
            const ours = oursKey ? (row[columns[0].key] ?? "").startsWith(oursKey) : false;
            return (
              <tr key={i} className="border-b border-dashed border-line last:border-b-0">
                {columns.map((c, ci) => (
                  <td
                    key={c.key}
                    className={cn(
                      "px-3.5 py-2 tabular-nums",
                      c.align === "right" && "text-right",
                      ci === 0 && highlightFirstCol && "font-semibold",
                      ours && "text-stamp",
                    )}
                  >
                    {row[c.key]}
                  </td>
                ))}
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}
