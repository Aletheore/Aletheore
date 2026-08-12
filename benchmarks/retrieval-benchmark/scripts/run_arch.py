import asyncio, json, sqlite3, sys
from pathlib import Path

BUDGET = 12000  # equal context budget per tool, filled in rank order
TOPK = 5

qs = _bench.load_questions("architecture")


def pack(pieces):
    """Fill the budget in rank order; never split a piece mid-way past the cap."""
    out, used = [], 0
    for p in pieces:
        if used >= BUDGET:
            break
        room = BUDGET - used
        chunk = p[:room]
        out.append(chunk)
        used += len(chunk)
    return "\n\n---\n\n".join(out)


# ---------- Aletheore ----------
from aletheore.search_index import search_index

REPO_A = Path(_bench.FLASK)
alet = {}
for q in qs:
    hits = search_index(REPO_A, q["q"], k=TOPK)
    pieces = []
    for h in hits:
        pieces.append(f"[{h.get('module_path')}::{h.get('symbol_name')} "
                      f"L{h.get('start_line')}-{h.get('end_line')}]\n{h.get('text','')}")
    alet[q["id"]] = pack(pieces)
    print("A", q["id"], len(alet[q["id"]]), file=sys.stderr)

# ---------- RepoWise ----------
from repowise.cli.commands.init_cmd import _resolve_embedder
from repowise.cli.providers.embedders import build_embedder
from repowise.core.persistence.vector_store import LanceDBVectorStore

REPO_R = _bench.FLASK_RW
db = sqlite3.connect(f"file:{REPO_R}/.repowise/wiki.db?mode=ro", uri=True)


def page_content(page_id, title):
    for tbl_q, arg in (
        ("select content from wiki_pages where id=?", page_id),
        ("select content from wiki_pages where title=?", title),
    ):
        try:
            r = db.execute(tbl_q, (arg,)).fetchone()
            if r and r[0]:
                return r[0]
        except Exception:
            pass
    return None


async def main():
    store = LanceDBVectorStore(f"{REPO_R}/.repowise/lancedb",
                               embedder=build_embedder(_resolve_embedder(None)))
    rw = {}
    for q in qs:
        res = await store.search(q["q"], limit=TOPK)
        pieces = []
        for r in res:
            body = page_content(r.page_id, r.title) or r.snippet or ""
            pieces.append(f"[{r.title} | {r.target_path}]\n{body}")
        rw[q["id"]] = pack(pieces)
        print("R", q["id"], len(rw[q["id"]]), res[0].search_type if res else "NONE",
              file=sys.stderr)
    await store.close()
    return rw


rw = asyncio.run(main())
json.dump([{"id": q["id"], "q": q["q"], "aletheore": alet[q["id"]], "repowise": rw[q["id"]]}
           for q in qs],
          open(os.path.join(_bench.OUT,"arch_context.json"), "w"), indent=2)
print("wrote arch_context.json", file=sys.stderr)
