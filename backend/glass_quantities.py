"""Shared glass quantity names and explicit, limited unit conversions."""
import math,re
CORE=['密度','显微硬度','杨氏模量','剪切模量','泊松比','玻璃转变温度','软化点','线膨胀系数','相对介电常数','介电损耗角正切','折射率','比热容','热导率']
SUMMARY={
 'density':('密度','g/cm³',1,20),'tg':('玻璃转变温度','°C',1,None),'mod_ung':('杨氏模量','GPa',1,None),'mod_sdv':('剪切模量','GPa',1,None),'mikrotv':('显微硬度','GPa',1,None),'pois':('泊松比','1',1,None),
 'any_tec':('线膨胀系数','ppm/K',.1,None),'nd300':('折射率','1',1,20),'eps730':('相对介电常数','1',1,None),'tgd730':('介电损耗角正切','1',.0001,None),
 'tsoft':('软化点','°C',1,None),'spt':('应变点','°C',1,None),'anpt':('退火点','°C',1,None),'lpt':('利特尔顿软化点','°C',1,None),'tliq':('液相线温度','°C',1,None),'tm':('熔融温度','°C',1,None),
 'cond220':('热导率','W/(m·K)',1,None),'rtsh180':('抗热震温差','K',1,None),'nud300':('阿贝数','1',1,20),'dnfc300':('平均色散','1',.0001,None),
 'spvm':('比体积','cm³/g',1,20),'tcr':('析晶特征温度','°C',1,None),'tx':('析晶特征温度','°C',1,None),'tn':('成核温度','°C',1,None),'io':('成核速率','cm⁻³·s⁻¹',1,None),'tmax':('最大晶体生长速率对应温度','°C',1,None),'vmax':('最大晶体生长速率','cm/s',1,None),'any_sut':('表面张力','mN/m',1,None),
}
for t in [55,100,160,210,350]:SUMMARY['tec'+str(t)]=('线膨胀系数','ppm/K',.1,t)
for t in [20,200,400,800,1000,1200,1400]:SUMMARY['cp'+str(t)]=('比热容','J/(kg·K)',1,t)
for t in [800,1000,1200,1400]:SUMMARY['dens'+str(t)]=('密度','g/cm³',1,t)
for t in [20,100,150,300,800,1000,1200,1400]:SUMMARY['ro'+str(t)]=('电阻率对数','log₁₀(Ω·cm)',1,t)
for t in [500,600,700,800,900,1000,1100,1200,1300,1400,1500,1600,1800,2000,2200]:SUMMARY['v'+str(t)]=('黏度对数','log₁₀(P)',1,t)
for t in range(1,14):SUMMARY['t'+str(t)]=('等黏度温度','°C',1,None)
for t in [900,1200,1300,1400]:SUMMARY['sut'+str(t)]=('表面张力','mN/m',1,t)
GROUP={'100':'密度','130':'线膨胀系数','180':'抗热震性能','200':'比热容','220':'热导率','230':'热扩散率','300':'光学性质','320':'光谱','400':'黏度','410':'黏度特征温度','420':'玻璃转变温度','440':'玻璃脆性指数','500':'弹性性质','516':'应力光学系数','518':'声学性质','520':'内耗','530':'显微硬度','540':'强度','560':'表面张力','600':'化学耐久性','700':'电输运性质','730':'介电性能','800':'离子扩散','830':'气体扩散与溶解','890':'磁学性质','900':'玻璃形成能力','950':'析晶特征温度','954':'熔融温度','990':'挥发'}
def number(v):
 try:
  x=float(v)
  return x if math.isfinite(x) else None
 except (TypeError,ValueError):return None

def original_quantity(code,alias,unit,multiplier,meta,value):
 """Never evaluate source formulas. Unknown conversions stay as labelled original quantities."""
 unit=(unit or '').strip();a=(alias or '').split('{')[0];tip=(meta.get('FldNam') or meta.get('CalcTip') or '').lower();kind=SUMMARY.get(tip,(GROUP.get(str(code),'原始性质'),'1',1,None))[0]
 if code=='730':kind='介电损耗角正切' if a=='tgd' else '相对介电常数' if a=='eps' else kind
 if code=='300':
  if a in ['nd','ne','n','nn','nc','nf','ng','ni']:kind='折射率'
  elif a.startswith('nu'):kind='阿贝数'
 if code=='700':kind='电阻率对数' if a.startswith('lsr') else '电导率对数' if a.startswith('lsc') else '电阻率' if a=='sr' else '电导率' if a=='sc' else kind
 if code=='400' and a.startswith('l'):kind='黏度对数'
 cond={};factors=re.findall(r'\b(fk|fm|fg|f|tc|tk|wlnm)\{([^{}]+)\}',meta.get('descriptor',''))
 for key,raw in factors:
  v=number(raw)
  if v is None:cond[key]=raw;continue
  if key in ['f','fk','fm','fg']:cond['频率Hz']=v*{'f':1,'fk':1e3,'fm':1e6,'fg':1e9}[key]
  elif key=='tc':cond['温度°C']=v
  elif key=='tk':cond['温度°C']=v-273.15
  elif key=='wlnm':cond['波长nm']=v
 n=number(value);scale=number(multiplier);actual=n/(scale or 1) if n is not None else None
 normunit=unit.lower().replace(' ','')
 known=True
 if normunit in ['g/cm3','g/cmm']:u='g/cm³'
 elif normunit=='gpa':u='GPa'
 elif normunit in ['oc','°c','c']:u='°C'
 elif normunit=='k':u='K'
 elif normunit in ['k^-1','1/k'] and kind=='线膨胀系数':u='ppm/K';actual=None if actual is None else actual*1e6
 elif not unit and kind in ['相对介电常数','介电损耗角正切','泊松比','折射率','阿贝数']:u='1'
 elif normunit in ['ohm.cm'] and kind in ['电阻率','电阻率对数']:u='log₁₀(Ω·cm)' if '对数' in kind else 'Ω·cm'
 elif normunit=='p' and kind=='黏度对数':u='log₁₀(P)'
 elif normunit in ['w/m.k','w/mk']:u='W/(m·K)'
 else:
  known=False;actual=n;u=unit or '原表未注明'
  if scale not in [None,1]:u+='（原值带倍率，见条件）'
 return kind,actual,u,known,cond

