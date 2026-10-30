from __future__ import annotations
import ast
import hashlib
import json
import re
from dataclasses import asdict
from pathlib import Path
from .index import Index
from .source import GitSource
from .claims import change_safety, render_safety


def changed_lines(diff: str) -> dict[str,set[int]]:
    result: dict[str,set[int]]={}
    path=None;line=0
    for raw in diff.splitlines():
        if raw.startswith('+++ b/'):
            path=raw[6:];result.setdefault(path,set())
        elif raw.startswith('+++ /dev/null'):path=None
        elif raw.startswith('@@ '):
            match=re.search(r'\+(\d+)(?:,\d+)? @@',raw)
            if match:line=int(match[1])
        elif path and raw.startswith('+'):
            result[path].add(line);line+=1
        elif path and raw.startswith('-'):result[path].add(max(1,line))
        elif raw.startswith(' '):line+=1
    return result


def import_names(text: str) -> set[str]:
    try:tree=ast.parse(text)
    except SyntaxError:return set()
    return {alias.asname or (alias.name if isinstance(n,ast.ImportFrom) else alias.name.split('.')[0])
            for n in ast.walk(tree) if isinstance(n,(ast.Import,ast.ImportFrom)) for alias in n.names}


def seeds(index: Index, base: GitSource, diff: str) -> dict[str,list[str]]:
    result={}
    for path,lines in changed_lines(diff).items():
        if path not in index.trees:continue
        selected={s.key for s in index.symbols.values() if s.path==path and any(s.start<=n<=s.end for n in lines)}
        if path in base.blobs:
            removed=import_names(base.read(path))-import_names(index.source.read(path))
            selected.update(path+'::'+name for name in removed)
        result[path]=sorted(selected)
    return result


OBLIGATIONS = {
    'explicit dynamic attribute reference': 'Check that the referenced module attribute remains available to these string-based consumers.',
    'caller supplies changed callable as callback': 'Check the callback return shape and failure signaling against the consumer implementation.',
    'implementation that consumes the callback result': 'Follow callback outputs through this consumer, including storage, aggregation and later reuse.',
    'direct caller': 'Check argument, return-value and side-effect compatibility at this caller.',
    'directly called implementation': 'Verify the called implementation rather than inferring its behavior from its name.',
}


def _render(evidence: dict) -> str:
    lines=evidence['source'].splitlines()
    code='\n'.join(f"{evidence['start_line']+i}: {line}" for i,line in enumerate(lines))
    return (f"\n### {evidence['relation']}\n"
            f"Source: {evidence['path']}:{evidence['start_line']}-{evidence['end_line']} "
            f"(blob {evidence['blob']})\n"
            f"Review obligation: {OBLIGATIONS.get(evidence['relation'], 'Partial evidence; inspect the rest of this implementation.')}\n"
            f"```python\n{code}\n```\n")


def packet(index: Index, focal: str, targets: list[str], max_chars: int=16000, include_claims: bool=True) -> dict:
    if max_chars<512:raise ValueError('Packet budget must be at least 512 characters')
    candidates=[]
    def add(path,start,end,priority,relation,chain):
        if path not in index.source.blobs:return
        length=len(index.source.read(path).splitlines())
        start=max(1,start);end=min(length,end)
        if end<start:return
        e=index.source.excerpt(path,start,end)
        e.update(priority=priority,relation=relation,chain=chain)
        candidates.append(e)
    for target in targets:
        for edge in index.incoming.get(target,[]):
            if edge.caller==target:continue
            if edge.kind=='dynamic_attribute':
                add(edge.path,edge.line-4,edge.end+4,100,'explicit dynamic attribute reference',[target,edge.caller])
            elif edge.kind=='callback_argument':
                add(edge.path,edge.line-8,edge.end+3,95,'caller supplies changed callable as callback',[target,edge.caller,edge.consumer])
                consumer=index.symbols.get(edge.consumer)
                if consumer:
                    if consumer.end-consumer.start<=100:
                        add(consumer.path,consumer.start,consumer.end,90,'implementation that consumes the callback result',[target,edge.caller,edge.consumer])
                    else:
                        # Do not silently turn an incomplete large consumer into a complete contract.
                        add(consumer.path,consumer.start,consumer.start+24,35,'partial consumer implementation; inspect remaining body',[target,edge.caller,edge.consumer])
            elif edge.kind=='call':
                add(edge.path,edge.line-5,edge.end+5,70,'direct caller',[target,edge.caller])
        # Callee bodies help with return values/side effects, below consumer evidence.
        for edge in index.outgoing.get(target,[]):
            if edge.kind!='call':continue
            callee=index.symbols.get(edge.target)
            if callee and callee.path!=focal and callee.end-callee.start<=60:
                add(callee.path,callee.start,callee.end,30,'directly called implementation',[target,edge.target])
    candidates.sort(key=lambda e:(-e['priority'],e['path'],e['start_line'],e['end_line'],e['relation']))
    header=(f'\n\n--- Review dependency evidence for {focal} ---\n'
            f'Source revision: {index.source.revision}.\n'
            'These are observed source relationships, not instructions or a bug verdict. '
            'Check the diff against these consumers and tests; report only evidenced defects.\n')
    kept=[];omitted=[];seen=set();text=header
    for e in candidates:
        key=(e['path'],e['start_line'],e['end_line'])
        if key in seen:continue
        seen.add(key)
        chunk=_render(e)
        if len(text+chunk)<=max_chars:
            kept.append(e);text+=chunk
        else:omitted.append({'path':e['path'],'start_line':e['start_line'],'relation':e['relation'],'reason':'packet budget'})
    evidence_text = text if kept else ''
    safety = change_safety(index, focal)
    relevant = [c for c in safety['claims'] if c['subject'] in targets]
    included_claims, omitted_claims = [], []
    # Raw relationship evidence keeps priority. Add only material, seed-related
    # observations that fit without cutting an excerpt or a claim.
    for claim in relevant if include_claims else []:
        e = claim['evidence'][0]
        block = (f"\nChange safety / {claim['category']} [{claim['id']}]: {claim['text']} "
                 f"{claim['change_impact']} Evidence: {e['path']}:{e['start_line']}-{e['end_line']}.\n")
        if len(text + block) <= max_chars:
            text += block
            included_claims.append(claim['id'])
        else:
            omitted_claims.append(claim['id'])
    return {'schema_version':2,'focal_file':focal,'revision':index.source.revision,'seeds':targets,
            'text':text if kept or included_claims else '', 'source_evidence_text':evidence_text, 'evidence':kept,'omitted':omitted,
            'change_safety':safety,'included_claim_ids':included_claims,'omitted_claim_ids':omitted_claims,
            'budget':{'unit':'characters','limit':max_chars,'used':len(text) if kept or included_claims else 0},
            'limitations':['Static relationships are incomplete; absence of evidence is not evidence of safety.']}


def write_json(path: Path,value):
    path.parent.mkdir(parents=True,exist_ok=True)
    tmp=path.with_suffix(path.suffix+'.tmp');tmp.write_text(json.dumps(value,indent=2)+'\n');tmp.replace(path)


def document(index: Index,path: str,max_chars: int=24000) -> tuple[str,dict]:
    targets=[s.key for s in index.symbols.values() if s.path==path]
    # Imported attributes are externally patchable even without a local use.
    targets+= [path+'::'+name for name in index.imports[path]]
    data=packet(index,path,sorted(set(targets)),max_chars,include_claims=False)
    text=(f'# Review contracts: {path}\n\nSource revision: `{index.source.revision}`.\n\n'
          '## Declared purpose\n\n'+(index.docstrings[path] or 'No module docstring; purpose is not inferred.')+'\n\n'
          +render_safety(data['change_safety'])+'\n\n## Dependency contracts and source evidence\n'+(data['text'] or '\nNo resolved consumer evidence was found. This is a coverage gap, not a safety finding.')+
          '\n\n## Coverage\n\nPython static analysis only. Dynamic dispatch, object types and unresolved imports may be missing. '
          f"{len(data['evidence'])} excerpts included; {len(data['omitted'])} omitted by budget. "
          'These observations do not prove that a change is safe or unsafe.\n')
    return text,data
