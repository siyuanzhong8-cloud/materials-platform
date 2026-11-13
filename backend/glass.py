"""Offline unified glass catalog API; source records and measurement conditions stay distinct."""
import csv,io,json,math,re,sqlite3
from pathlib import Path
from functools import lru_cache
from fastapi import HTTPException,Query
from fastapi.responses import FileResponse,Response,StreamingResponse
from backend.glass_quantities import CORE
from backend.intent_search import parse_intent,filter_list,point_clause
ROOT=Path(__file__).resolve().parents[1]
def db():
 c=sqlite3.connect((ROOT/'data/glass_catalog.sqlite').as_uri()+'?mode=ro',uri=True);c.row_factory=sqlite3.Row;return c
@lru_cache(maxsize=1)
def summary_data():return json.loads((ROOT/'data/catalog_summary.json').read_text('utf-8'))
def where(q='',components='',match='contains',system='',kind='',unit='',lo=None,hi=None,pcb=False,source='',family='',filters='[]',exclude_components=''):
 w=['1=1'];p=[]
 if q.strip():
  tokens=re.findall(r'[\w]+',q.strip(),re.UNICODE)
  if tokens:w.append('m.id IN (SELECT rowid FROM material_search WHERE material_search MATCH ?)');p.append(' AND '.join('"'+x.replace('"','""')+'"*' for x in tokens[:12]))
 parts=sorted(set(x.strip() for x in components.split(',') if x.strip()))
 if parts:
  if match=='exact':w.append('m.system=?');p.append(' / '.join(parts))
  else:
   w.append('m.id IN (SELECT material_id FROM component_members WHERE component IN ('+','.join('?' for _ in parts)+') GROUP BY material_id HAVING count(*)=?)');p+=parts+[len(parts)]
 if system:w.append('m.system=?');p.append(system)
 if family:w.append('m.family=?');p.append(family)
 if source:w.append('EXISTS(SELECT 1 FROM entries e WHERE e.material_id=m.id AND e.source=?)');p.append(source)
 if pcb:w.append('m.pcb=1')
 if (lo is not None or hi is not None) and not kind:raise HTTPException(400,'请先选择性质与单位')
 if kind:
  pw=['p.material_id=m.id','p.kind=?'];pp=[kind]
  if unit:pw.append('p.unit=?');pp.append(unit)
  if lo is not None or hi is not None:
   pw.append('p.filterable=1')
   if not unit:
    units={r['unit'] for r in summary_data()['property_kinds'] if r['kind']==kind and r['filterable']}
    if len(units)>1:raise HTTPException(400,'该性质有多种单位，请选择单位后筛选')
   for v,op in [(lo,'>='),(hi,'<=')]:
    if v is not None:
     if not math.isfinite(v):raise HTTPException(400,'请输入有限数值')
     pw.append('p.value'+op+'?');pp.append(v)
  w.append('EXISTS(SELECT 1 FROM properties p WHERE '+' AND '.join(pw)+')');p+=pp
 for f in filter_list(filters):
  clause,args=point_clause(f);w.append('EXISTS(SELECT 1 FROM properties p WHERE p.material_id=m.id AND '+clause+')');p+=args
 excluded=sorted(set(x.strip() for x in exclude_components.split(',') if x.strip()))
 if excluded:
  w.append('NOT EXISTS(SELECT 1 FROM component_members cm WHERE cm.material_id=m.id AND cm.component IN ('+','.join('?' for _ in excluded)+'))');p+=excluded
 return ' AND '.join(w),p

@lru_cache(maxsize=512)
def base_metadata(bid):
 with db() as c:return json.loads(c.execute('SELECT payload FROM entry_metadata_base WHERE id=?',(bid,)).fetchone()[0])

def read_metadata(raw):
 m=json.loads(raw);bid=m.pop('__base',None)
 return dict(base_metadata(bid),**m) if bid else m

def material_dict(row):
 d=dict(row);d['comp']=json.loads(d['comp']);return d

def public_property(row):
 d=dict(row);d['conditions']=json.loads(d.pop('payload'));d['source']=d.pop('source');d.pop('metadata',None);return d

def property_rows(c,mid,kind='',origin='',offset=0,limit=40):
 sql='p.material_id=?';args=[mid]
 if kind:sql+=' AND p.kind=?';args.append(kind)
 if origin:sql+=' AND p.origin=?';args.append(origin)
 n=c.execute('SELECT count(*) FROM properties p WHERE '+sql,args).fetchone()[0]
 rr=c.execute('''SELECT p.*,co.payload,e.source,e.table_id,e.glass_no FROM properties p JOIN conditions co ON co.id=p.condition_id JOIN entries e ON e.id=p.entry_id WHERE '''+sql+''' ORDER BY CASE p.origin WHEN '原表解析' THEN 0 WHEN '文献实验' THEN 1 WHEN '原始实验表' THEN 2 WHEN '厂商目录' THEN 3 ELSE 4 END,p.id LIMIT ? OFFSET ?''',args+[limit,offset])
 return {'items':[public_property(r) for r in rr],'total':n,'offset':offset,'limit':limit}

def safe(v):
 if not isinstance(v,str):return v
 return "'"+v if v.startswith(('=','+','-','@','\t','\r')) else v

PREVIEW_KINDS=('密度','玻璃转变温度','杨氏模量','折射率','线膨胀系数','相对介电常数')
SORT_COLUMNS={'property_count':'m.property_count','id':'m.id','name':'m.name COLLATE NOCASE','system':'m.system COLLATE NOCASE','records':'m.record_count'}

def order_clause(sort,order,applied):
 direction='ASC' if order=='asc' else 'DESC'
 if sort=='match' and applied:
  clause,args=point_clause(applied[0])
  expr='(SELECT p.value FROM properties p WHERE p.material_id=m.id AND '+clause+" ORDER BY CASE p.origin WHEN '性质汇总' THEN 1 ELSE 0 END,p.id LIMIT 1)"
  return expr+' '+direction+' NULLS LAST, m.id',args
 col=SORT_COLUMNS.get(sort,'m.property_count')
 if sort not in SORT_COLUMNS:direction='DESC'
 return col+' '+direction+', m.id',[]

def attach_extras(c,items):
 if not items:return
 ids=[item['id'] for item in items]
 ph=','.join('?' for _ in ids)
 sources={}
 for r in c.execute('SELECT material_id,source,count(*) n FROM entries WHERE material_id IN ('+ph+') GROUP BY material_id,source ORDER BY n DESC',ids):
  sources.setdefault(r['material_id'],[]).append({'source':r['source'],'n':r['n']})
 kph=','.join('?' for _ in PREVIEW_KINDS)
 sql=('WITH ranked AS (SELECT p.id,ROW_NUMBER() OVER (PARTITION BY p.material_id,p.kind ORDER BY CASE p.origin WHEN \'原表解析\' THEN 0 WHEN \'文献实验\' THEN 1 WHEN \'原始实验表\' THEN 2 WHEN \'厂商目录\' THEN 3 ELSE 4 END,p.id) rn FROM properties p WHERE p.material_id IN ('+ph+') AND p.kind IN ('+kph+') AND p.value IS NOT NULL) '
      'SELECT p.*,co.payload,e.source,e.table_id,e.glass_no FROM ranked r JOIN properties p ON p.id=r.id JOIN conditions co ON co.id=p.condition_id JOIN entries e ON e.id=p.entry_id WHERE r.rn=1')
 previews={}
 for r in c.execute(sql,ids+list(PREVIEW_KINDS)):
  previews.setdefault(r['material_id'],[]).append(public_property(r))
 for item in items:
  item['sources']=sources.get(item['id'],[])
  item['previews']=previews.get(item['id'],[])


PLOT_AXES={
 'nd':('折射率 nd','折射率','1',('ND300','dt 300 nd','dt 300 nsd')),
 'vd':('阿贝数 νd','阿贝数','1',('NUD300','dt 300 nud','dt 300 nusd')),
 'tg':('玻璃转变温度','玻璃转变温度','°C',None),
 'density':('密度','密度','g/cm³',None),
 'cte':('线膨胀系数','线膨胀系数','ppm/K',None),
 'dk':('相对介电常数','相对介电常数','1',None),
 'df':('介电损耗角正切','介电损耗角正切','1',None),
 'modulus':('杨氏模量','杨氏模量','GPa',None),
}
def axis_cte(name,spec,basis):
 if spec.startswith('comp:'):
  comp=spec[5:].strip()[:40]
  if not comp:raise HTTPException(400,'请选择组分')
  return name+' AS (SELECT m2.id material_id,CAST(json_extract(m2.comp,?) AS REAL) value FROM component_members cm JOIN materials m2 ON m2.id=cm.material_id WHERE cm.component=? AND m2.basis=?)',['$."'+comp.replace('"','')+'"',comp,basis],{'key':spec,'label':comp+' 含量','unit':basis}
 if spec not in PLOT_AXES:raise HTTPException(400,'不支持的坐标')
 label,kind,unit,labels=PLOT_AXES[spec]
 sql=name+' AS (SELECT material_id,value,MIN(id) FROM properties WHERE kind=? AND filterable=1 AND unit=? AND value IS NOT NULL'
 args=[kind,unit]
 if labels:sql+=' AND label IN ('+','.join('?' for _ in labels)+')';args+=list(labels)
 return sql+' GROUP BY material_id)',args,{'key':spec,'label':label,'unit':unit}
def quantile(sorted_vals,q):
 if not sorted_vals:return None
 i=min(len(sorted_vals)-1,max(0,int(round(q*(len(sorted_vals)-1)))));return sorted_vals[i]
def basis_components(comp):
 return sorted(((k,float(v)) for k,v in comp.items() if isinstance(v,(int,float)) and v>0),key=lambda x:-x[1])

def register(app):
 @app.get('/api/glass/summary')
 def summary():return summary_data()
 @app.get('/api/glass/interpret')
 def interpret(text:str=Query('',max_length=500)):
  return parse_intent(text,[x['name'] for x in summary_data()['components']])
 @app.get('/api/glass/search')
 def search(q:str=Query('',max_length=200),components:str=Query('',max_length=1500),match:str='contains',system:str=Query('',max_length=3000),kind:str='',unit:str='',lo:float|None=None,hi:float|None=None,pcb:bool=False,source:str='',family:str='',offset:int=Query(0,ge=0),limit:int=Query(24,ge=1,le=100),view:str='systems',filters:str=Query('[]',max_length=10000),exclude_components:str=Query('',max_length=1500),sort:str='property_count',order:str='desc'):
  w,p=where(q,components,match,system,kind,unit,lo,hi,pcb,source,family,filters,exclude_components)
  applied=filter_list(filters)
  if kind:applied.append({'kind':kind,'unit':unit,'lo':lo,'hi':hi})
  if order not in ('asc','desc'):order='desc'
  if sort not in set(SORT_COLUMNS)|{'match'}:sort='property_count'
  ranking,rank_args=order_clause(sort,order,applied)
  with db() as c:
   if view=='systems':
    if w=='1=1':
     total=summary_data()['systems'];items=[dict(r) for r in c.execute('SELECT system,n,records,properties FROM catalog_systems ORDER BY n DESC,system LIMIT ? OFFSET ?',(limit,offset))]
    else:
     base=' FROM materials m WHERE '+w+" AND m.system!=''";total=c.execute('SELECT count(distinct m.system)'+base,p).fetchone()[0];items=[dict(r) for r in c.execute('SELECT m.system,count(*) n,sum(m.record_count) records,sum(m.property_count) properties'+base+' GROUP BY m.system ORDER BY n DESC,m.system LIMIT ? OFFSET ?',p+[limit,offset])]
   else:
    total=c.execute('SELECT count(*) FROM materials m WHERE '+w,p).fetchone()[0]
    items=[material_dict(r) for r in c.execute('SELECT m.* FROM materials m WHERE '+w+' ORDER BY '+ranking+' LIMIT ? OFFSET ?',p+rank_args+[limit,offset])]
    attach_extras(c,items)
    if applied:
     for item in items:
      item['matches']=[]
      for f in applied:
       clause,args=point_clause(f)
       row=c.execute('SELECT p.*,co.payload,e.source,e.table_id,e.glass_no FROM properties p JOIN conditions co ON co.id=p.condition_id JOIN entries e ON e.id=p.entry_id WHERE p.material_id=? AND '+clause+" ORDER BY CASE p.origin WHEN '性质汇总' THEN 1 ELSE 0 END,p.id LIMIT 1",[item['id']]+args).fetchone()
       if row:item['matches'].append(public_property(row))
   return {'items':items,'total':total,'offset':offset,'limit':limit,'view':view,'sort':sort,'order':order,'applied':applied}
 @app.get('/api/glass/recipe/{mid}')
 def detail(mid:int):
  with db() as c:
   row=c.execute('select * from materials where id=?',(mid,)).fetchone()
   if not row:raise HTTPException(404,'未找到配方')
   d=material_dict(row);d['kinds']=[dict(r) for r in c.execute('SELECT kind,count(*) n FROM properties WHERE material_id=? GROUP BY kind ORDER BY n DESC',(mid,))];d['sources']=[dict(r) for r in c.execute('SELECT source,count(*) n FROM entries WHERE material_id=? GROUP BY source',(mid,))]
   d['entries']=[]
   for r in c.execute('SELECT * FROM entries WHERE material_id=? ORDER BY id LIMIT 40',(mid,)):
    item=dict(r);item['composition']=json.loads(item['composition']);item['metadata']=read_metadata(item['metadata']);d['entries'].append(item)
   d['basic']=[]
   for kind in CORE:
    rr=c.execute('''SELECT p.*,co.payload,e.source,e.table_id,e.glass_no FROM properties p JOIN conditions co ON co.id=p.condition_id JOIN entries e ON e.id=p.entry_id WHERE p.material_id=? AND p.kind=? AND p.value IS NOT NULL ORDER BY CASE p.origin WHEN '原表解析' THEN 0 WHEN '文献实验' THEN 1 WHEN '原始实验表' THEN 2 WHEN '厂商目录' THEN 3 ELSE 4 END,p.id LIMIT 3''',(mid,kind))
    d['basic'].append({'kind':kind,'items':[public_property(r) for r in rr]})
   d['properties']=property_rows(c,mid);d['optical']=[{'id':r['id'],'source_path':r['source_path']} for r in c.execute('SELECT id,source_path FROM optical_data WHERE material_id=?',(mid,))];return d
 @app.get('/api/glass/plot')
 def plot(x:str='vd',y:str='nd',basis:str='mol%',q:str=Query('',max_length=200),components:str=Query('',max_length=1500),match:str='contains',system:str=Query('',max_length=3000),kind:str='',unit:str='',lo:float|None=None,hi:float|None=None,pcb:bool=False,source:str='',family:str='',filters:str=Query('[]',max_length=10000),exclude_components:str=Query('',max_length=1500),limit:int=Query(3000,ge=100,le=6000)):
  if basis not in ('mol%','at%','wt%'):basis='mol%'
  w,p=where(q,components,match,system,kind,unit,lo,hi,pcb,source,family,filters,exclude_components)
  xs,xa,xi=axis_cte('xs',x,basis);ys,ya,yi=axis_cte('ys',y,basis)
  sql='WITH '+xs+','+ys+' SELECT m.id,m.name,m.system,xs.value x,ys.value y FROM xs JOIN ys ON ys.material_id=xs.material_id JOIN materials m ON m.id=xs.material_id WHERE xs.value IS NOT NULL AND ys.value IS NOT NULL AND '+w
  with db() as c:rows=[dict(r) for r in c.execute(sql,xa+ya+p)]
  total=len(rows)
  bounds={}
  for ax in ('x','y'):
   vals=sorted(r[ax] for r in rows)
   bounds[ax]=(quantile(vals,.005),quantile(vals,.995)) if len(vals)>=200 else (vals[0] if vals else None,vals[-1] if vals else None)
  kept=[r for r in rows if bounds['x'][0]<=r['x']<=bounds['x'][1] and bounds['y'][0]<=r['y']<=bounds['y'][1]] if rows else []
  hidden=total-len(kept)
  if len(kept)>limit:
   step=len(kept)/limit;kept=[kept[int(i*step)] for i in range(limit)]
  return {'x':xi,'y':yi,'total':total,'hidden_outliers':hidden,'shown':len(kept),'points':[[r['id'],r['x'],r['y'],r['name'] or r['system']] for r in kept]}
 @app.get('/api/glass/recipe/{mid}/similar')
 def similar(mid:int,limit:int=Query(8,ge=1,le=20)):
  with db() as c:
   row=c.execute('SELECT * FROM materials WHERE id=?',(mid,)).fetchone()
   if not row:raise HTTPException(404,'未找到配方')
   comp=json.loads(row['comp'] or '{}');parts=basis_components(comp)
   if row['basis'] not in ('mol%','at%','wt%') or not parts:return {'items':[],'basis':row['basis'],'note':'该条目没有可比较的定量组分'}
   tot=sum(v for _,v in parts) or 1
   target={k:v/tot*100 for k,v in parts}
   anchor=[k for k,_ in parts[:3]]
   cand=c.execute('SELECT m.id,m.name,m.family,m.basis,m.comp,m.system,m.record_count,m.property_count FROM materials m WHERE m.basis=? AND m.id!=? AND m.id IN (SELECT material_id FROM component_members WHERE component IN ('+','.join('?' for _ in anchor)+') GROUP BY material_id HAVING count(*)=?) LIMIT 30000',[row['basis'],mid]+anchor+[len(anchor)]).fetchall()
   scored=[]
   for r in cand:
    cc=dict(basis_components(json.loads(r['comp'] or '{}')))
    s=sum(cc.values())
    if not s:continue
    cc={k:v/s*100 for k,v in cc.items()}
    d=sum(abs(target.get(k,0)-cc.get(k,0)) for k in set(target)|set(cc))/2
    scored.append((d,-(r['property_count'] or 0),r))
   scored.sort(key=lambda z:(z[0],z[1]))
   items=[]
   for d,_,r in scored[:limit]:
    item=material_dict(r);item['distance']=round(d,2);items.append(item)
   attach_extras(c,items)
  return {'items':items,'basis':row['basis'],'candidates':len(cand)}
 @app.get('/api/glass/recipe/{mid}/properties')
 def properties(mid:int,kind:str='',origin:str='',offset:int=Query(0,ge=0),limit:int=Query(40,ge=1,le=100)):
  with db() as c:return property_rows(c,mid,kind,origin,offset,limit)
 @app.get('/api/glass/entry/{eid}/original')
 def original(eid:int):
  with db() as c:r=c.execute('SELECT * FROM entries WHERE id=?',(eid,)).fetchone()
  if not r:raise HTTPException(404)
  meta=read_metadata(r['metadata']);source=r['source'];parts=[]
  if source=='SciGlass':
   if meta.get('legacy_id'):
    with sqlite3.connect((ROOT/'data/glass.sqlite').as_uri()+'?mode=ro',uri=True) as old:
     orig=old.execute('SELECT s.file FROM recipes r JOIN sources s ON s.id=r.table_id WHERE r.id=?',(meta['legacy_id'],)).fetchone()
    if orig:
     p=ROOT/'glass_originals'/Path(orig[0]).name
     if p.is_file():return FileResponse(p,filename=p.name)
   with sqlite3.connect((ROOT/'data/glass_archive.sqlite').as_uri()+'?mode=ro',uri=True) as archive:
    rr=archive.execute('SELECT original_info FROM source_tables WHERE kod=?',(r['table_id'],)).fetchone()
    if rr:parts=[['原始表编号',r['table_id']],['原始表完整数据',rr[0]]]
  elif source in ['OHARA','SCHOTT','HOYA']:
   p=ROOT/'data/supplementary'/(source+'_'+Path(meta.get('source_file','')).name)
   if p.is_file():return FileResponse(p,filename=p.name)
  elif source=='New Glass Forum':
   name=str(r['glass_no'])[0].lower()+'-propty.xls';p=ROOT/'data/supplementary'/('NewGlassForum_'+name)
   if p.is_file():return FileResponse(p,filename=p.name)
  if not parts:parts=[['字段','原始值']]+[[k,json.dumps(v,ensure_ascii=False) if isinstance(v,(dict,list)) else str(v)] for k,v in meta.items()]
  f=io.StringIO();csv.writer(f).writerows([[safe(v) for v in row] for row in parts]);return Response(('\ufeff'+f.getvalue()).encode(),media_type='text/csv',headers={'Content-Disposition':f'attachment; filename="source_{eid}.csv"'})
 @app.get('/api/glass/export')
 def export(q:str='',components:str='',match:str='contains',system:str='',kind:str='',unit:str='',lo:float|None=None,hi:float|None=None,pcb:bool=False,source:str='',family:str='',filters:str=Query('[]',max_length=10000),exclude_components:str=Query('',max_length=1500)):
  w,p=where(q[:200],components[:1500],match,system[:3000],kind,unit,lo,hi,pcb,source,family,filters,exclude_components)
  def generate():
   f=io.StringIO();writer=csv.writer(f);writer.writerow(['配方ID','名称','材料类别','配方口径','组成_JSON','组分组合','来源记录数','性质记录数']);yield '\ufeff'+f.getvalue();f.seek(0);f.truncate(0)
   with db() as c:
    for r in c.execute('SELECT m.id,m.name,m.family,m.basis,m.comp,m.system,m.record_count,m.property_count FROM materials m WHERE '+w+' ORDER BY m.id',p):
     writer.writerow([safe(v) for v in r]);yield f.getvalue();f.seek(0);f.truncate(0)
  return StreamingResponse(generate(),media_type='text/csv',headers={'Content-Disposition':'attachment; filename="glass_recipes.csv"'})
 @app.get('/api/glass/recipe/{mid}/export')
 def export_properties(mid:int):
  def generate():
   f=io.StringIO();w=csv.writer(f);w.writerow(['配方ID','性质','数值','单位','原始值','原单位','频率Hz','温度C','数据类型','来源','源表编号','玻璃编号','原始标签','条件_JSON']);yield '\ufeff'+f.getvalue();f.seek(0);f.truncate(0)
   with db() as c:
    for r in c.execute('''SELECT p.material_id,p.kind,p.value,p.unit,p.raw_value,p.raw_unit,p.freq,p.temp,p.origin,e.source,e.table_id,e.glass_no,p.label,co.payload FROM properties p JOIN entries e ON e.id=p.entry_id JOIN conditions co ON co.id=p.condition_id WHERE p.material_id=? ORDER BY p.kind,p.id''',(mid,)):
     w.writerow([safe(v) for v in r]);yield f.getvalue();f.seek(0);f.truncate(0)
  return StreamingResponse(generate(),media_type='text/csv',headers={'Content-Disposition':f'attachment; filename="glass_{mid}_properties.csv"'})
 @app.get('/api/glass/attachments')
 def attachments():
  with db() as c:return [dict(r) for r in c.execute('SELECT * FROM attachments ORDER BY id')]
 @app.get('/api/glass/attachment/{aid}')
 def attachment(aid:int):
  with db() as c:r=c.execute('SELECT * FROM attachments WHERE id=?',(aid,)).fetchone()
  if not r:raise HTTPException(404)
  p=ROOT/'data/supplementary'/Path(r['file']).name
  if not p.is_file():raise HTTPException(404)
  return FileResponse(p,filename=p.name)
 @app.get('/api/glass/optical/{oid}')
 def optical(oid:int):
  with db() as c:r=c.execute('SELECT payload FROM optical_data WHERE id=?',(oid,)).fetchone()
  if not r:raise HTTPException(404)
  return json.loads(r[0])
 @app.get('/catalyst')
 def catalyst():return FileResponse(ROOT/'frontend/dist/catalyst.html')
 @app.get('/')
 def home():return FileResponse(ROOT/'frontend/dist/portal.html')
