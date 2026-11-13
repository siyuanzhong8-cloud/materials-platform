"""Local, explicit query grammar. Unrecognized text remains a keyword, never a guessed filter."""
import json,math,re,unicodedata
from fastapi import HTTPException
N=r'[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?'
SPECS=[('玻璃转变温度','°C',['玻璃转变温度','玻璃化转变温度','Tg']),('介电损耗角正切','1',['介电损耗角正切','损耗角正切','介电损耗','tanδ','tan delta','Df']),('相对介电常数','1',['相对介电常数','介电常数','Dk']),('线膨胀系数','ppm/K',['线膨胀系数','热膨胀系数','膨胀系数','CTE']),('维氏硬度','kgf/mm²',['维氏硬度','维克氏硬度']),('显微硬度','GPa',['显微硬度','硬度']),('杨氏模量','GPa',['杨氏模量','弹性模量','Young模量']),('剪切模量','GPa',['剪切模量']),('密度','g/cm³',['密度','density']),('折射率','1',['折射率']),('阿贝数','1',['阿贝数','色散系数','Abbe数']),('软化点','°C',['软化点','软化温度']),('泊松比','1',['泊松比']),('比热容','J/(kg·K)',['比热容','比热']),('热导率','W/(m·K)',['热导率','导热系数'])]
ALIASES={a:(k,u) for k,u,aa in SPECS for a in aa};NAME=re.compile('|'.join(re.escape(a) for a in sorted(ALIASES,key=len,reverse=True)),re.I)
UNIT=r'(?:g\s*/\s*cm(?:3|³|\^3)|kg\s*/\s*m(?:3|³|\^3)|GPa|MPa|ppm\s*/\s*K|10\s*\^?\s*[-−]6\s*/\s*K|10\s*\^?\s*[-−]7\s*/\s*K|℃|°C|摄氏度|°K|K|J/\(kg[·*]?K\)|W/\(m[·*]?K\)|kgf/mm(?:2|²)|无量纲)'
def parse_intent(text,components):
 if len(text)>500:raise HTTPException(400,'搜索内容最多500字')
 t=unicodedata.normalize('NFKC',text).translate(str.maketrans('₀₁₂₃₄₅₆₇₈₉','0123456789')).replace('−','-').replace('～','~').replace('：',':')
 work=t;out={'components':[],'exclude_components':[],'filters':[],'q':'','source':'','pcb':False,'warnings':[]}
 for name,formula in [('二氧化硅','SiO2'),('氧化硅','SiO2'),('氧化硼','B2O3'),('氧化铝','Al2O3'),('氧化镁','MgO'),('氧化钙','CaO'),('氧化钠','Na2O'),('氧化钾','K2O'),('氧化锂','Li2O'),('氧化锌','ZnO')]:work=work.replace(name,' '+formula+' ')
 freq=None;temp=None
 fm=re.search(r'(?:频率\s*(?:为|是|=|:)?\s*)?('+N+r')\s*(GHz|MHz|kHz|Hz)',work,re.I)
 if fm:freq=float(fm[1])*{'ghz':1e9,'mhz':1e6,'khz':1e3,'hz':1}[fm[2].lower()];work=work[:fm.start()]+' '+work[fm.end():]
 tm=re.search(r'(?:(?:测试温度|测量温度)\s*(?:为|是|=|:)?\s*|在(?='+N+r'\s*(?:°C|℃|摄氏度|K)(?:下|时)))('+N+r')\s*(°C|℃|摄氏度|K)(?:下|时)?',work,re.I)
 if tm:temp=float(tm[1])-(273.15 if tm[2].upper()=='K' else 0);work=work[:tm.start()]+' '+work[tm.end():]
 # Use the known database components, with longest formula first (CaO2 must not become CaO).
 for co in sorted(components,key=len,reverse=True):
  if not re.fullmatch(r'[A-Za-z][A-Za-z0-9().+\-]*',co):continue
  pat=re.compile(r'(?<![A-Za-z0-9°/])'+re.escape(co)+r'(?![A-Za-z0-9])',re.I)
  matches=list(pat.finditer(work))
  if not matches:continue
  for m in reversed(matches):
   pre=work[max(0,m.start()-5):m.start()];target='exclude_components' if re.search(r'(?:不含|排除|不要)\s*$',pre) else 'components'
   if co in ['C','K','P'] and re.search(r'\d\s*$',pre):continue
   if re.fullmatch(r'[A-Z][a-z]?',co) and (re.match(r'-[A-Za-z0-9]',work[m.end():]) or re.search(r'[A-Za-z0-9]-$',pre)):continue
   out[target].append(co);work=work[:m.start()]+' '+work[m.end():]
 if '无碱' in work:out['exclude_components']+=['Na2O','K2O','Li2O'];work=work.replace('无碱',' ')
 for name,cs in [('铝硼硅酸盐',['Al2O3','B2O3','SiO2']),('硼铝硅酸盐',['Al2O3','B2O3','SiO2']),('硼硅酸盐',['B2O3','SiO2']),('硼硅',['B2O3','SiO2']),('铝硅酸盐',['Al2O3','SiO2']),('钠钙硅',['Na2O','CaO','SiO2']),('硅酸盐',['SiO2']),('磷酸盐',['P2O5'])]:
  if name in work:out['components']+=cs;work=work.replace(name,' ')
 if re.search(r'\bPCB\b',work,re.I):out['pcb']=True;work=re.sub(r'\bPCB\b',' ',work,flags=re.I)
 for source in ['SCHOTT','OHARA','HOYA','BIMOG','Matbench','SciGlass']:
  if re.search(re.escape(source),work,re.I):out['source']=source;work=re.sub(re.escape(source),' ',work,flags=re.I)
 filters=[];spans=[];matches=list(NAME.finditer(work))
 for i,m in enumerate(matches):
  end=matches[i+1].start() if i+1<len(matches) else len(work);tail=work[m.end():end];tail=re.sub(r'^\s*(?:在|为|是|约|范围|介于|:|=)\s*','',tail)
  actual_alias=next(a for a in ALIASES if a.lower()==m[0].lower());kind,unit=ALIASES[actual_alias];lo=hi=None;li=ui=True;used=0
  ran=re.match(r'\s*('+N+r')\s*('+UNIT+r')?\s*(?:~|至|到|—|–|-)\s*('+N+r')\s*('+UNIT+r')?',tail,re.I)
  if ran:lo=float(ran[1]);hi=float(ran[3]);given=ran[4] or ran[2];used=ran.end()
  else:
   one=re.match(r'\s*(<=|>=|≤|≥|<|>|不高于|不超过|不大于|不低于|不少于|至少|最高|最低|小于等于|大于等于|小于|大于|低于|高于|等于)?\s*('+N+r')\s*('+UNIT+r')?\s*(以下|以内|以上)?',tail,re.I)
   if not one:out['warnings'].append('“'+kind+'”缺少可识别的范围，请在高级筛选中设置。');spans.append((m.start(),m.end()));continue
   op=one[1] or ('<=' if one[4] in ['以下','以内'] else '>=' if one[4]=='以上' else '=');v=float(one[2]);given=one[3];used=one.end()
   if op in ['<','小于','低于']:hi=v;ui=False
   elif op in ['<=','≤','不高于','不超过','不大于','最高','小于等于']:hi=v
   elif op in ['>','大于','高于']:lo=v;li=False
   elif op in ['>=','≥','不低于','不少于','至少','最低','大于等于']:lo=v
   else:lo=hi=v
  if not all(v is None or math.isfinite(v) for v in [lo,hi]):raise HTTPException(400,'范围必须是有限数值')
  if lo is not None and hi is not None and lo>hi:raise HTTPException(400,kind+'的最小值不能大于最大值')
  scale=1;shift=0
  if given:
   g=given.lower().replace(' ','')
   if unit=='g/cm³' and g.startswith('kg'):scale=.001
   elif unit=='GPa' and g=='mpa':scale=.001
   elif unit=='°C' and g in ['k','°k']:shift=-273.15
   elif unit=='ppm/K' and '7/' in g:scale=.1
   elif unit=='ppm/K' and g.startswith('10'):scale=1
   elif not (g in ({'g/cm³','g/cm3','g/cm^3'} if unit=='g/cm³' else {'℃','°c','摄氏度'} if unit=='°C' else {'无量纲'} if unit=='1' else {unit.lower().replace(' ','')})):
    out['warnings'].append(kind+'的单位“'+given+'”与默认口径不一致，请核对高级筛选。');spans.append((m.start(),end));continue
  f={'kind':kind,'unit':unit,'lo':None if lo is None else float(format(lo*scale+shift,'.15g')),'hi':None if hi is None else float(format(hi*scale+shift,'.15g')),'lo_inclusive':li,'hi_inclusive':ui}
  if freq is not None and kind in ['相对介电常数','介电损耗角正切']:f['freq']=freq
  if temp is not None:f['temp']=temp
  filters.append(f)
  # Remove only the matched property clause; preserve model names and unknown words as keywords.
  raw_tail=work[m.end():end];offset=len(raw_tail)-len(tail);spans.append((m.start(),m.end()+offset+used))
 for start,end in reversed(spans):work=work[:start]+' '+work[end:]
 work=re.sub(r'(?:帮我|给我|我想要|我需要|想找|查找|搜索|寻找|找到|找一下|找|请|一种|一些|具有|材料|玻璃|的|并且|而且|同时|以及|包含|含有|含|不含|排除|不要|和|与|在|为|是|约|之间|范围内|范围|之内)',' ',work)
 work=re.sub(r'[,，;；。:：、/\s]+',' ',work).strip()
 out['q']=work;out['filters']=filters;out['components']=sorted(set(out['components']));out['exclude_components']=sorted(set(out['exclude_components']))
 if freq is not None and not any('freq' in f for f in filters):out['warnings'].append('已输入频率，但未指定介电性质范围；频率未用于筛选。')
 if temp is not None and not filters:out['warnings'].append('已输入测试温度，请同时指定需要筛选的性质。')
 return out

def filter_list(raw):
 try:items=json.loads(raw or '[]')
 except (TypeError,ValueError):raise HTTPException(400,'性质筛选条件格式不正确')
 if not isinstance(items,list) or len(items)>8:raise HTTPException(400,'一次最多筛选8个性质条件')
 for f in items:
  if not isinstance(f,dict) or not isinstance(f.get('kind'),str) or not isinstance(f.get('unit'),str):raise HTTPException(400,'请指定性质及单位')
  for key in ['lo','hi','freq','temp']:
   if f.get(key) is not None and (isinstance(f[key],bool) or not isinstance(f[key],(int,float)) or not math.isfinite(f[key])):raise HTTPException(400,'筛选范围必须是有限数值')
  if f.get('lo') is not None and f.get('hi') is not None and f['lo']>f['hi']:raise HTTPException(400,'最小值不能大于最大值')
 return items

def point_clause(f):
 kinds=[f['kind']];units=[f['unit']]
 if f['kind']=='线膨胀系数':kinds+=['线膨胀系数α']
 if f['unit']=='g/cm³':units+=['g/cm3']
 if f['unit']=='°C':units+=['℃']
 w=['p.filterable=1','p.kind IN ('+','.join('?' for _ in kinds)+')','p.unit IN ('+','.join('?' for _ in units)+')'];args=kinds+units
 for key,op in [('lo','>=' if f.get('lo_inclusive',True) else '>'),('hi','<=' if f.get('hi_inclusive',True) else '<')]:
  if f.get(key) is not None:w.append('p.value'+op+'?');args.append(f[key])
 for key in ['freq','temp']:
  if f.get(key) is not None:w.append('abs(p.'+key+'-?)<=?');args.extend([f[key],max(1e-6,abs(f[key])*1e-7)])
 return ' AND '.join(w),args
