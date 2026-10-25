#!/usr/bin/env python3
"""Conservative public price collector.

Supported sources:
- structured HTTPS JSON feeds supplied for reuse
- Tokubai store page, where automated access is permitted by robots.txt and provider terms.
No authentication, challenge bypass, image OCR, or inferred prices.
"""
from __future__ import annotations
import datetime as dt
import json
import os
from pathlib import Path
import re
import unicodedata
from decimal import Decimal, ROUND_HALF_UP
from urllib.request import Request, urlopen
from urllib.parse import urlparse, urlsplit, urlunsplit, quote
from urllib.robotparser import RobotFileParser
from html.parser import HTMLParser

ROOT=Path(__file__).resolve().parent
JST=dt.timezone(dt.timedelta(hours=9))
USER_AGENT='NeighborhoodPriceDashboard/1.0 (low-rate noncommercial availability checker)'
TIMEOUT=18
PRICE_RE=re.compile(r'(?P<n>\d{1,3}(?:,\d{3})*(?:\.\d{1,2})?)\s*円\s*[(（]\s*税込\s*[)）]')
DATE_RE=re.compile(r'(?:(?:20\d{2})年)?\s*(\d{1,2})月\s*(\d{1,2})日')


def load(name,default=None):
    path=ROOT/name
    return json.loads(path.read_text(encoding='utf-8')) if path.exists() else default

def write(name,obj):
    (ROOT/name).write_text(json.dumps(obj,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')

def now_jst():return dt.datetime.now(JST)

def get_text(url, *, max_bytes=1200000):
    p=urlparse(url)
    if p.scheme != 'https' or not p.hostname:raise ValueError('HTTPS URL required')
    # urllib requires ASCII-encoded paths for Japanese retailer URLs.
    bits=urlsplit(url)
    request_url=urlunsplit((bits.scheme,bits.netloc,quote(bits.path,safe='/%'),quote(bits.query,safe='=&%'),bits.fragment))
    req=Request(request_url,headers={'User-Agent':USER_AGENT,'Accept':'text/html,application/json'})
    with urlopen(req,timeout=TIMEOUT) as resp:
        final=urlparse(resp.geturl())
        if final.scheme!='https' or final.hostname!=p.hostname:raise ValueError('Redirected to different host')
        data=resp.read(max_bytes+1)
        if len(data)>max_bytes:raise ValueError('Source size limit exceeded')
        return data.decode('utf-8','replace')

def robots_allows(url):
    p=urlparse(url)
    robots=f'{p.scheme}://{p.netloc}/robots.txt'
    try:
        text=get_text(robots,max_bytes=200000)
    except Exception as exc:
        raise ValueError('robots.txt unavailable; skipped conservatively: '+str(exc)[:130])
    rp=RobotFileParser();rp.parse(text.splitlines())
    if not rp.can_fetch(USER_AGENT,url):raise ValueError('robots.txt blocks collection')
    return True

def validate_date(value):
    if not isinstance(value,str):return None
    try:
        return dt.datetime.fromisoformat(value.replace('Z','+00:00'))
    except ValueError:return None

def valid_until_from_text(text, today):
    """Only explicit end-dates are trusted. Otherwise expire at end of collection day."""
    m=re.search(r'(?:まで|期間|期限|\d+日\s*\([^)]*\)\s*まで)',text)
    # Require a date adjacent to a 'まで' phrase to avoid treating flyer issue date as expiry.
    expiry=re.search(r'(\d{1,2})月\s*(\d{1,2})日\s*(?:[（(][^)）]*[)）])?\s*まで',text)
    if expiry:
        month,day=map(int,expiry.groups())
        year=today.year
        try:
            candidate=dt.date(year,month,day)
            if (candidate-today).days < -180:candidate=dt.date(year+1,month,day)
            if (candidate-today).days<=31:return candidate.isoformat()
        except ValueError:pass
    return today.isoformat()

class _ProductBlocks(HTMLParser):
    """Read self-contained HTML product cards without third-party dependencies."""
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.stack=[]
        self.blocks=[]
        self.suppressed=0
    def handle_starttag(self,tag,attrs):
        attrs=dict(attrs)
        if tag in ('script','style','noscript','nav','header','footer'):
            self.suppressed+=1
        cl=attrs.get('class','').lower()
        href=attrs.get('href','')
        candidate=tag in ('article','li','div','a') and (
            any(v in cl for v in ('featured_product','product_item','product-card','product_card'))
            or (tag=='a' and 'office_featured_product' in href))
        self.stack.append([tag,candidate,[]])
    def handle_startendtag(self,tag,attrs):
        # Void tags must not remain on the stack.
        pass
    def handle_data(self,data):
        if not self.suppressed:
            for _,candidate,parts in self.stack:
                if candidate:parts.append(data)
    def handle_endtag(self,tag):
        if tag in ('script','style','noscript','nav','header','footer'):
            self.suppressed=max(0,self.suppressed-1)
        # Ignore unmatched closing tags, but recover valid HTML nesting.
        for i in range(len(self.stack)-1,-1,-1):
            if self.stack[i][0]==tag:
                for item in self.stack[i:]:
                    if item[1]:
                        content=' '.join(' '.join(item[2]).split())
                        if 4<len(content)<430 and PRICE_RE.search(content):
                            self.blocks.append(content)
                del self.stack[i:]
                return


def extract_tokubai(html,store_id,url,now):
    """Extract only product-sized HTML cards with explicit tax-inclusive yen prices.

    This is a conservative, versioned adapter. Ambiguous entries are rejected;
    unsupported retailer markup needs a separately tested parser.
    """
    parser=_ProductBlocks()
    parser.feed(html)
    candidates=sorted(parser.blocks,key=len)
    rows=[];seen=set();today=now.date()
    for raw in candidates:
        prices=PRICE_RE.findall(raw)
        if not prices:continue
        # Some cards show 178.20 yen and a rounded 178 yen in two labels.
        # Accept only if both represent the same displayed yen amount.
        rounded={int(Decimal(x.replace(',','')).quantize(Decimal('1'),rounding=ROUND_HALF_UP)) for x in prices}
        if len(rounded)!=1:continue
        amount=Decimal(prices[0].replace(',',''))
        if amount<=0 or amount>100000:continue
        price=int(amount.quantize(Decimal('1'),rounding=ROUND_HALF_UP))
        before=raw[:PRICE_RE.search(raw).start()].strip()
        before=re.sub(r'\s+\d[\d,]*(?:\.\d+)?\s*円\s*$', '',before)
        before=re.sub(r'\s+',' ',before)
        if not (2<=len(before)<=130):continue
        if any(x in before for x in ('チラシを見る','一覧を見る','店舗情報詳細','口コミを見る')):continue
        valid=valid_until_from_text(raw,today)
        key=(before,price)
        if key in seen:continue
        seen.add(key)
        size_match=re.search(r'(\d+(?:\.\d+)?\s*(?:g|kg|ml|L|ｇ|ｍｌ|個|本|枚|袋|パック|コ入|カン)|100\s*g(?:当たり|あたり))',before,re.I)
        rows.append({'name':before,'store_id':store_id,'price_yen':price,
                     'size':size_match.group(1) if size_match else '',
                     'source_url':url,'checked_at':now.isoformat(),'valid_until':valid,
                     'sale':False,'source_type':'featured_listing','listing_type':'featured',
                     'channel':'in_store','comparison_confidence':'unverified'})
    return rows[:80]

def check_row(p,valid_stores,now):
    if not isinstance(p,dict):return None
    required=('name','store_id','price_yen','source_url','checked_at')
    if not all(k in p and p[k] is not None for k in required):return None
    if p['store_id'] not in valid_stores:return None
    if not isinstance(p['name'],str) or len(p['name'].strip())<2:return None
    if isinstance(p['price_yen'],bool) or not isinstance(p['price_yen'],(int,float)) or not 0<p['price_yen']<1000000:return None
    if urlparse(str(p['source_url'])).scheme!='https':return None
    checked=validate_date(p['checked_at'])
    if not checked or not checked.tzinfo:return None
    if not -dt.timedelta(minutes=10)<=now-checked<=dt.timedelta(days=2):return None
    expiry=p.get('valid_until')
    if expiry:
        try:
            if dt.date.fromisoformat(expiry)<now.date():return None
        except (ValueError,TypeError):return None
    row=dict(p)
    row['name']=row['name'].strip()
    # Labels must be explicit, never infer 'regular price' from sale=False.
    if row.get('listing_type') not in ('regular','featured','promotion','online_reference','unknown'):
        row['listing_type']='unknown'
    if row.get('channel') not in ('in_store','online_reference','unverified'):
        row['channel']='unverified'
    row['price_yen']=int(Decimal(str(row['price_yen'])).quantize(Decimal('1'),rounding=ROUND_HALF_UP))
    return row

def history_key(p):
    # This is only a within-store history key; it does not prove cross-store identity.
    def clean(v):
        return ''.join(unicodedata.normalize('NFKC',str(v or '')).lower().split())
    return clean(p['name'])+'|'+clean(p.get('size'))+'|'+p['store_id']

def merge_history(existing,rows,now):
    history=list(existing) if isinstance(existing,list) else []
    today=now.date().isoformat()
    today_prices={history_key(p):p for p in rows}
    # A product can have multiple current listings with different variants: latest
    # one per exact product wording+size+store is retained for the day's snapshot.
    history=[v for v in history if v.get('checked_day')!=today or v.get('item_key') not in today_prices]
    for key,p in today_prices.items():
        history.append({'item_key':key,'checked_day':today,'price_yen':p['price_yen']})
    cutoff=(now.date()-dt.timedelta(days=90)).isoformat()
    history=[h for h in history if isinstance(h,dict) and str(h.get('checked_day',''))>=cutoff]
    return history[-5000:]

def collect():
    cfg=load('sources.json',{'sources':[]})
    db=load('prices.json',{'stores':[],'prices':[]})
    now=now_jst()
    valid_stores={s['id'] for s in db['stores']}
    rows=[]; status=[]
    for src in cfg.get('sources',[]):
        source_name=src.get('name','unnamed')
        if not src.get('enabled',False):
            status.append({'source':source_name,'status':'disabled','items':0})
            continue
        try:
            url=src['url'];typ=src['type'];store=src.get('store_id')
            if typ=='tokubai_html':
                if store not in valid_stores:raise ValueError('Unknown store_id')
                if urlparse(url).hostname!='tokubai.co.jp':raise ValueError('Invalid host for Tokubai adapter')
                robots_allows(url)
                data=extract_tokubai(get_text(url),store,url,now)
            elif typ=='json_feed':
                if not src.get('reuse_permitted',False):raise ValueError('Set reuse_permitted=true only after obtaining permission')
                payload=json.loads(get_text(url))
                data=payload['prices']
                if not isinstance(data,list):raise ValueError('prices must be an array')
                # A provider-approved catalog can declare verified shelf vs online
                # reference scope. Do not silently equate delivery and in-store price.
                scoped=[]
                for item in data:
                    if not isinstance(item,dict):continue
                    v=dict(item)
                    v.setdefault('listing_type',src.get('listing_type','unknown'))
                    v.setdefault('channel',src.get('channel','unverified'))
                    scoped.append(v)
                data=scoped
            else:raise ValueError('Unsupported source type')
            accepted=[v for p in data if (v:=check_row(p,valid_stores,now))]
            rows.extend(accepted)
            status.append({'source':source_name,'status':'ok' if accepted else 'no_valid_products','items':len(accepted)})
        except Exception as exc:
            status.append({'source':source_name,'status':'error','items':0,'reason':str(exc)[:220]})
    # Replace only with current, proven products. Never carry stale prices into today.
    # If every enabled source failed, leave old file intact, but show status so UI can reject stale entries.
    enabled=[s for s in status if s['status']!='disabled']
    failed=bool(enabled) and all(s['status']=='error' for s in enabled)
    if not failed:
        dedup={}
        for p in rows:
            dedup[(p['store_id'],p['name'],p['price_yen'])]=p
        db['prices']=list(dedup.values())
        db['price_history']=merge_history(db.get('price_history',[]),db['prices'],now)
        db['updated_at']=now.isoformat() if rows else None
        write('prices.json',db)
    write('collection_status.json',{'checked_at':now.isoformat(),'sources':len(enabled),
              'items':len(rows),'failed_all':failed,'results':status})
    print(json.dumps({'items':len(rows),'status':status},ensure_ascii=False))
    if failed:raise SystemExit('All price sources failed. Existing data unchanged; freshness rules apply.')

if __name__=='__main__': collect()
