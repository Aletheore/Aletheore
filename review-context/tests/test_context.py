import subprocess
import tempfile
import unittest
from pathlib import Path
from review_context.index import Index
from review_context.packets import changed_lines, document, packet, seeds
from review_context.source import GitSource

class ContextTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.repo=Path(self.temp.name)
        self.git('init','-q')
        self.git('config','user.email','test@example.invalid');self.git('config','user.name','Context Test')
    def git(self,*args):
        return subprocess.check_output(['git','-C',str(self.repo),*args],text=True).strip()
    def commit(self,files):
        for name,text in files.items():
            p=self.repo/name;p.parent.mkdir(parents=True,exist_ok=True);p.write_text(text)
        self.git('add','.');self.git('commit','-qm','fixture')
        return self.git('rev-parse','HEAD')
    def context(self,base,head,path,limit=16000):
        source=GitSource(self.repo,head);index=Index(source)
        diff=source.git('diff','--unified=0',base,head)
        selected=seeds(index,GitSource(self.repo,base),diff)
        return index,packet(index,path,selected[path],limit)
    def test_removed_import_discovers_string_patch_consumers(self):
        base=self.commit({'src/pkg/service.py':'def pick(): return 1\n',
            'src/pkg/worker.py':'from pkg.service import pick\n\ndef run(): return 2\n',
            'tests/test_worker.py':'def test_run(monkeypatch):\n    monkeypatch.setattr("pkg.worker.pick", lambda: 3)\n'})
        head=self.commit({'src/pkg/worker.py':'\ndef run(): return 2\n'})
        index,p=self.context(base,head,'src/pkg/worker.py')
        self.assertIn('pkg.worker.pick',p['text'])
        self.assertEqual(p['evidence'][0]['relation'],'explicit dynamic attribute reference')
        self.assertNotIn('test_worker.py',p['seeds'])
    def test_callback_discovers_unchanged_consumer_and_storage(self):
        base=self.commit({'pkg/store.py':'def batch(jobs, compute):\n    result = compute(jobs)\n    cache.put_many(result)\n    return result\n',
            'pkg/task.py':'from pkg.store import batch as cached\n\ndef inspect_many(jobs):\n    return [inspect_one(x) for x in jobs]\n\ndef run(jobs):\n    return cached(jobs, compute=inspect_many)\n'})
        head=self.commit({'pkg/task.py':'from pkg.store import batch as cached\n\ndef inspect_many(jobs):\n    try:\n        return [inspect_one(x) for x in jobs]\n    except RuntimeError:\n        return [[] for x in jobs]\n\ndef run(jobs):\n    return cached(jobs, compute=inspect_many)\n'})
        index,p=self.context(base,head,'pkg/task.py')
        self.assertIn('cache.put_many(result)',p['text'])
        self.assertIn('compute=inspect_many',p['text'])
        self.assertTrue(any(e['relation']=='implementation that consumes the callback result' for e in p['evidence']))
        self.assertNotIn('poison',p['text'])
    def test_unrelated_same_name_function_is_not_a_caller(self):
        base=self.commit({'a.py':'def work(): return 1\n','b.py':'def work(): return 9\n\ndef unrelated(): return work()\n'})
        head=self.commit({'a.py':'def work(): return 2\n'})
        _,p=self.context(base,head,'a.py')
        self.assertEqual(p['evidence'],[])
    def test_ambiguous_module_suffix_is_not_guessed(self):
        rev=self.commit({'a/pkg/mod.py':'def run(): pass\n','b/pkg/mod.py':'def run(): pass\n',
                         'test_mod.py':'def t(monkeypatch):\n    monkeypatch.setattr("pkg.mod.run", 0)\n'})
        index=Index(GitSource(self.repo,rev))
        self.assertIsNone(index.module('pkg.mod'))
        self.assertFalse(any(e.kind=='dynamic_attribute' for e in index.edges))
    def test_budget_preserves_whole_excerpts_and_reports_omissions(self):
        rev=self.commit({'pkg/a.py':'def work(): return 1\n','test_a.py':'def t(monkeypatch):\n    monkeypatch.setattr("pkg.a.work", lambda: "'+('x'*700)+'")\n'})
        index=Index(GitSource(self.repo,rev))
        p=packet(index,'pkg/a.py',['pkg/a.py::work'],512)
        self.assertEqual(p['evidence'],[])
        self.assertEqual(p['text'],'')
        self.assertEqual(len(p['omitted']),1)
        self.assertEqual(p,packet(index,'pkg/a.py',['pkg/a.py::work'],512))
    def test_revision_is_pinned_and_dirty_files_are_not_read(self):
        rev=self.commit({'a.py':'def f(): return "committed"\n'})
        (self.repo/'a.py').write_text('uncommitted secret')
        src=GitSource(self.repo,rev)
        self.assertIn('committed',src.read('a.py'));self.assertNotIn('secret',src.read('a.py'))
        with self.assertRaises(ValueError):src.read('../outside')
    def test_excerpts_have_reproducible_source_and_hash(self):
        rev=self.commit({'a.py':'first\nsecond\nthird\n'})
        e=GitSource(self.repo,rev).excerpt('a.py',2,3)
        self.assertEqual(e['source'],'second\nthird')
        import hashlib
        self.assertEqual(e['sha256'],hashlib.sha256(e['source'].encode()).hexdigest())
    def test_unsupported_syntax_and_language_are_reported(self):
        rev=self.commit({'a.py':'def broken(\n','b.ts':'export const value=1;\n','ok.py':'def ok(): return 1\n'})
        index=Index(GitSource(self.repo,rev))
        self.assertIn('a.py',index.unsupported);self.assertIn('b.ts',index.unsupported)
        text,data=document(index,'ok.py')
        self.assertIn('coverage gap',text)
        self.assertEqual(data['evidence'],[])
    def test_cli_writes_documents_and_refuses_stale_output(self):
        rev=self.commit({'a.py':'def f(): return 1\n'})
        out=self.repo/'output'
        import sys,json
        command=[sys.executable,'-m','review_context','build','--repo',str(self.repo),'--revision',rev,'--output',str(out)]
        root=Path(__file__).resolve().parents[1]
        subprocess.run(command,cwd=root,check=True,capture_output=True)
        self.assertEqual(json.loads((out/'manifest.json').read_text())['revision'],rev)
        self.assertTrue((out/'files/a.py.md').exists())
        self.assertTrue((out/'relationships.json').exists())
        self.assertTrue((out/'routing.json').exists())
        self.assertIn('Change safety',(out/'files/a.py.md').read_text())
        before=(out/'manifest.json').read_bytes()
        again=subprocess.run(command,cwd=root,capture_output=True)
        self.assertNotEqual(again.returncode,0)
        self.assertEqual((out/'manifest.json').read_bytes(),before)

    def test_change_safety_is_mandatory_and_grounded(self):
        from review_context.claims import change_safety, validate_claim, SECTIONS
        rev=self.commit({'a.py':'import os\n\ndef work():\n    assert os.getenv("MODE")\n    with open("x") as stream:\n        try:\n            return stream.read()\n        except OSError:\n            stream.close()\n            raise\n'})
        index=Index(GitSource(self.repo,rev));data=change_safety(index,'a.py')
        self.assertEqual(set(data['sections']),set(SECTIONS))
        for category in ['invariants','configuration','lifecycle','failure']:
            self.assertTrue(data['sections'][category]['claim_ids'])
        for c in data['claims']:validate_claim(index,c)
        text,_=document(index,'a.py')
        self.assertIn('## Change safety',text)
        self.assertIn('No supported claim extracted',text)

    def test_materiality_does_not_restate_symbol_inventory(self):
        from review_context.claims import change_safety
        rev=self.commit({'a.py':'def first(): return 1\n\ndef second(): return 2\n'})
        data=change_safety(Index(GitSource(self.repo,rev)),'a.py')
        self.assertFalse(data['claims'])
        self.assertTrue(all(s['gap'] for s in data['sections'].values()))

    def test_nested_call_edges_are_stable_across_process_hash_seeds(self):
        import os,sys
        rev=self.commit({'dep.py':'def f(*args): return args\n',
                         'a.py':'from dep import f\n\ndef g():\n    return f(f(\n        1),\n        2)\n'})
        code=('from review_context.index import Index; from review_context.source import GitSource; '
              'import json,sys; print(json.dumps(Index(GitSource(sys.argv[1],sys.argv[2])).serialized(),sort_keys=True))')
        outputs=[]
        for seed in ['1','2','3','4']:
            outputs.append(subprocess.check_output([sys.executable,'-c',code,str(self.repo),rev],
                cwd=Path(__file__).resolve().parents[1],env=dict(os.environ,PYTHONHASHSEED=seed)))
        self.assertTrue(all(data==outputs[0] for data in outputs))

    def test_repeated_observation_does_not_duplicate_claims(self):
        from review_context.claims import change_safety
        rev=self.commit({'a.py':'def work(resource):\n    resource.close()\n    resource.close()\n'})
        data=change_safety(Index(GitSource(self.repo,rev)),'a.py')
        self.assertEqual(len(data['claims']),1)

    def test_stale_and_edited_claims_are_rejected(self):
        from review_context.claims import change_safety, validate_claim
        import copy
        rev=self.commit({'a.py':'def f(x):\n    assert x > 0\n'})
        index=Index(GitSource(self.repo,rev));claim=change_safety(index,'a.py')['claims'][0]
        edited=copy.deepcopy(claim);edited['text']='This is always safe'
        with self.assertRaises(ValueError):validate_claim(index,edited)
        newer=self.commit({'other.py':'def g(): pass\n'})
        with self.assertRaises(ValueError):validate_claim(Index(GitSource(self.repo,newer)),claim)

    def test_failure_actions_outside_header_have_their_own_evidence(self):
        from review_context.claims import change_safety
        source='def f():\n    try:\n        work()\n    except ValueError:\n'+''.join(f'        x{i} = {i}\n' for i in range(20))+'        return []\n'
        rev=self.commit({'a.py':source})
        claims=change_safety(Index(GitSource(self.repo,rev)),'a.py')['claims']
        handler=next(c for c in claims if c['text'].startswith('Handles'))
        self.assertTrue(any('return []' in e['source'] for e in handler['evidence']))

    def test_graph_rejects_invented_relationships(self):
        from review_context.workflows import relationships,validate_relationship
        import copy
        rev=self.commit({'a.py':'def f(): return 1\n\ndef g(): return f()\n'})
        index=Index(GitSource(self.repo,rev));edge=relationships(index)[0]
        validate_relationship(index,edge)
        fake=copy.deepcopy(edge);fake['to']='missing.py::imaginary'
        with self.assertRaises(ValueError):validate_relationship(index,fake)

    def test_callback_workflow_is_supported_and_bounded(self):
        from review_context.workflows import workflows,validate_workflow
        rev=self.commit({'store.py':'def consume(jobs, compute): return compute(jobs)\n',
                         'worker.py':'from store import consume\n\ndef task(jobs): return []\n\ndef run(jobs): return consume(jobs, task)\n'})
        index=Index(GitSource(self.repo,rev));edges,flows=workflows(index,targets={'worker.py::task'})
        self.assertTrue(flows);self.assertLessEqual(len(flows),5)
        for f in flows:validate_workflow(index,f)
        self.assertTrue(any(s['label']=='callback_argument' for f in flows for s in f['steps']))
        self.assertFalse(workflows(index,limit=0)[1])
        self.assertFalse(workflows(index,targets={'missing.py::nope'})[1])

    def test_valid_but_disconnected_workflow_is_rejected(self):
        from review_context.workflows import relationships,validate_workflow
        from review_context.claims import fingerprint
        rev=self.commit({'a.py':'def f(): pass\n\ndef run(): f()\n', 'b.py':'def g(): pass\n\ndef run(): g()\n'})
        index=Index(GitSource(self.repo,rev));edges=relationships(index)
        flow={'revision':rev,'steps':edges,'relationship_ids':[e['id'] for e in edges]}
        flow['id']=fingerprint(flow)
        with self.assertRaises(ValueError):validate_workflow(index,flow)

    def test_routing_links_test_consumers(self):
        from review_context.workflows import workflows,routing
        rev=self.commit({'pkg/worker.py':'def work(): pass\n',
            'tests/test_work.py':'def test_work(monkeypatch):\n    monkeypatch.setattr("pkg.worker.work", 1)\n'})
        index=Index(GitSource(self.repo,rev));_,flows=workflows(index)
        row=routing(index,['pkg/worker.py'],flows)['if_you_change']['pkg/worker.py']
        self.assertEqual(row['tests'],['tests/test_work.py'])
        self.assertTrue(row['workflows'])

    def test_parameter_shadowing_and_local_imports_are_not_global_edges(self):
        rev=self.commit({'dep.py':'def work(): pass\n',
            'a.py':'from dep import work\n\ndef shadow(work): return work()\n\ndef local():\n    from dep import work as compute\n    return compute()\n\ndef outside(): return compute()\n'})
        index=Index(GitSource(self.repo,rev))
        edges=[e for e in index.edges if e.kind=='call']
        self.assertFalse(any(e.caller=='a.py::shadow' for e in edges))
        self.assertFalse(any(e.caller=='a.py::outside' for e in edges))
        self.assertTrue(any(e.caller=='a.py::local' and e.target=='dep.py::work' for e in edges))

    def test_budget_preserves_material_claim_omissions(self):
        rev=self.commit({'a.py':'def work():\n    assert value == "'+('x'*700)+'"\n'})
        index=Index(GitSource(self.repo,rev));data=packet(index,'a.py',['a.py::work'],512)
        self.assertLessEqual(len(data['text']),512)
        self.assertTrue(data['omitted_claim_ids'])
        self.assertTrue(data['change_safety']['claims'])

    def test_deleted_and_context_lines_do_not_become_added_lines(self):
        raw='--- a/a.py\n+++ b/a.py\n@@ -3,2 +3,2 @@\n-old\n+new\n context\n'
        self.assertEqual(changed_lines(raw),{'a.py':{3}})

if __name__=='__main__':unittest.main()
