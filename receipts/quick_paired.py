import json,re,collections,random,sys
rows=[json.loads(l) for l in open('stage6.jsonl')]
r=[x for x in rows if x['model']=='indierouter/deepseek-v4-flash' and 11<=int(re.match(r'f(\d+)',x['fixture']).group(1))<=40 and not x.get('error')]
by={(x['fixture'],x['condition'],x['rep']):x for x in r}
fx=sorted({x['fixture'] for x in r}); random.seed(7)
print("counts",dict(collections.Counter(x['condition'] for x in r)))
def row(cond):
    pairs=[(by[(f,cond,k)],by[(f,'baseline',k)]) for f in fx for k in (1,2,3) if (f,cond,k) in by and (f,'baseline',k) in by]
    if not pairs: return
    w=sum(a['passed'] and not b['passed'] for a,b in pairs); l=sum(b['passed'] and not a['passed'] for a,b in pairs)
    pc=sum(a['passed'] for a,_ in pairs)/len(pairs); pb=sum(b['passed'] for _,b in pairs)/len(pairs)
    sc=sum(a['score'] for a,_ in pairs)/len(pairs)-sum(b['score'] for _,b in pairs)/len(pairs)
    tok=sum(a['tokens_total'] for a,_ in pairs)/sum(b['tokens_total'] for _,b in pairs)
    fxs=sorted({a['fixture'] for a,_ in pairs}); bs=[]
    for _ in range(2000):
        d=[]
        for f in [random.choice(fxs) for _ in fxs]:
            d+=[a['passed']-b['passed'] for a,b in pairs if a['fixture']==f]
        bs.append(sum(d)/len(d))
    bs.sort()
    print(f"{cond:8} pairs {len(pairs):3} | pass {pc:.0%} vs baseline {pb:.0%} | diff {pc-pb:+.1%} 95% CI [{bs[50]:+.1%},{bs[1949]:+.1%}] | wins/losses {w}/{l} | score diff {sc:+.3f} | tokens x{tok:.2f}")
for c in ('hallbook','hint','inject','oracle','ask'): row(c)
