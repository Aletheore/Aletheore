export function FileTree({ files, readers }: { files: readonly { name: string; body: string }[]; readers: readonly string[] }) {
  return (
    <div className="grid gap-10 lg:grid-cols-[1.25fr_0.75fr]">
      <div className="font-display text-[13px] leading-[1.7]">
        <p className="text-ink">.aletheore/</p>
        {files.map((f, i) => (
          <div key={f.name} className="term-line grid grid-cols-[auto_1fr] gap-x-3 border-l border-dashed border-line-strong pl-4" style={{ animationDelay: `${i * 90}ms` }}>
            <span className="text-faint" aria-hidden="true">{i === files.length - 1 ? "└──" : "├──"}</span>
            <div className="pb-4">
              <span className="font-bold text-ink">{f.name}</span>
              <p className="mt-0.5 max-w-[56ch] font-sans text-[13px] leading-[1.6] text-muted">{f.body}</p>
            </div>
          </div>
        ))}
      </div>
      <div>
        <p className="mb-3 font-display text-[11.5px] uppercase tracking-[0.06em] text-faint">Everything below reads it</p>
        <ul className="flex flex-wrap gap-1.5">
          {readers.map((r) => (
            <li key={r} className="rounded-full border border-line-strong px-3 py-1 font-display text-[11.5px] text-muted">{r}</li>
          ))}
        </ul>
      </div>
    </div>
  );
}
