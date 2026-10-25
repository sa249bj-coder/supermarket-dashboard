"""Collect structured price feeds only; never fabricate or scrape unsupported sites."""
import json, pathlib, datetime, urllib.request, urllib.parse
ROOT=pathlib.Path(__file__).parent
cfg=json.loads((ROOT/'sources.json').read_text(encoding='utf-8'))
base=json.loads((ROOT/'prices.json').read_text(encoding='utf-8'))
now=datetime.datetime.now(datetime.timezone(datetime.timedelta(hours=9)))
rows=[]; errors=[]
for src in cfg['sources']:
    try:
        url=src['url']; parsed=urllib.parse.urlparse(url)
        if parsed.scheme!='https': raise ValueError('HTTPS required')
        req=urllib.request.Request(url,headers={'User-Agent':'NeighborhoodPriceDashboard/0.1 (public structured feed)'})
        with urllib.request.urlopen(req,timeout=15) as r:
            payload=json.load(r)
        if not isinstance(payload.get('prices'),list):raise ValueError('prices array missing')
        for p in payload['prices']:
            if not all(k in p for k in ('name','store_id','price_yen','source_url','checked_at')):continue
            if not isinstance(p['price_yen'],(int,float)) or p['price_yen']<0:continue
            if p['store_id'] not in {s['id'] for s in base['stores']}:continue
            if not p['source_url'].startswith('https://'):continue
            rows.append(p)
    except Exception as exc:errors.append({'source':src.get('name','unknown'),'error':str(exc)})
# Fail closed: do not replace valid feed with empty list if every source failed.
if cfg['sources'] and not rows and errors: raise SystemExit('All configured sources failed: '+str(errors))
base['prices']=rows
base['updated_at']=now.isoformat() if cfg['sources'] else None
(ROOT/'prices.json').write_text(json.dumps(base,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
(ROOT/'collection_status.json').write_text(json.dumps({'checked_at':now.isoformat(),'sources':len(cfg['sources']),'items':len(rows),'errors':errors},ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
print('items',len(rows),'errors',len(errors))
