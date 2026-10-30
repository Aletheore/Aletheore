from __future__ import annotations
import argparse
import hashlib
import json
from pathlib import Path
from .index import Index
from .packets import document,packet,seeds,write_json
from .source import GitSource
from .claims import render_safety
from .workflows import workflows, routing, render_workflow, render_routes, validate_workflow


def main():
    parser=argparse.ArgumentParser(description='Source-backed dependency documentation for code review; no model calls.')
    subs=parser.add_subparsers(dest='command',required=True)
    for name in ['build','pr']:
        p=subs.add_parser(name)
        p.add_argument('--repo',required=True,type=Path)
        p.add_argument('--output',required=True,type=Path)
        p.add_argument('--include',action='append',default=[],help='Repository-relative source root; repeatable')
        p.add_argument('--max-chars',type=int,default=16000)
        if name=='build':
            p.add_argument('--revision',required=True)
            p.add_argument('--file',action='append',default=[],help='Limit rendered pages; still index all included source')
        else:
            p.add_argument('--base',required=True);p.add_argument('--head',required=True)
    args=parser.parse_args()
    source=GitSource(args.repo,args.revision if args.command=='build' else args.head)
    index=Index(source,tuple(args.include))
    if args.output.exists() and any(args.output.iterdir()):
        parser.error('Output must be empty; use a new revision-specific directory to avoid stale documents')
    args.output.mkdir(parents=True,exist_ok=True)
    focus = None
    if args.command == 'pr':
        base=GitSource(args.repo,args.base)
        diff=source.git('diff','--no-ext-diff','--no-renames','--unified=0',base.revision,source.revision,'--')
        selected=seeds(index,base,diff)
        focus={target for targets in selected.values() for target in targets}
    relation_list, flow_list = workflows(index, targets=focus)
    write_json(args.output/'relationships.json', relation_list)
    write_json(args.output/'workflows.json', flow_list)
    for flow in flow_list:
        validate_workflow(index, flow)
        page = args.output/'workflows'/(flow['id']+'.md')
        page.parent.mkdir(parents=True,exist_ok=True)
        page.write_text(render_workflow(flow))
    write_json(args.output/'index.json',index.serialized())
    if args.command=='build':
        selected=args.file or sorted(index.trees)
        for path in selected:
            if path not in index.trees:parser.error(f'File not indexed: {path}')
            text,data=document(index,path,args.max_chars)
            page=args.output/'files'/(path+'.md');page.parent.mkdir(parents=True,exist_ok=True);page.write_text(text)
            write_json(args.output/'contracts'/(path+'.json'),data)
        manifest={'revision':source.revision,'pages':len(selected),'kind':'repository dependency documentation'}
    else:
        packets={path:packet(index,path,targets,args.max_chars) for path,targets in selected.items()}
        write_json(args.output/'packets.json',packets)
        for path,data in packets.items():
            page=args.output/'files'/(path+'.md');page.parent.mkdir(parents=True,exist_ok=True)
            page.write_text('# PR dependency context: '+path+'\n'+render_safety(data['change_safety'])+'\n'+data['source_evidence_text']+'\n')
        selected=list(selected)
        manifest={'base':base.revision,'head':source.revision,'kind':'PR dependency evidence',
                  'changed_indexed_files':len(packets),'files_with_evidence':sum(bool(p['text']) for p in packets.values()),
                  'changed_unindexed_files':sorted(set(source.git('diff','--name-only',base.revision,source.revision).splitlines())-set(packets))}
    route_data = routing(index, list(selected), flow_list)
    write_json(args.output/'routing.json', route_data)
    (args.output/'README.md').write_text(render_routes(route_data))
    manifest.update(schema_version=2,documentation_version='change-safety-v2',
                    relationship_count=len(relation_list),workflow_count=len(flow_list),includes=args.include,index_sha256=hashlib.sha256((args.output/'index.json').read_bytes()).hexdigest(),
                    source_selection='static dependency graph; no gold labels or case-specific symbol names',coverage=index.serialized()['coverage'])
    write_json(args.output/'manifest.json',manifest)
    print(json.dumps({k:v for k,v in manifest.items() if k!='coverage'},indent=2))

if __name__=='__main__':main()
