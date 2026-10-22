"""Original dynamic status image; no player data is baked into templates."""
from PIL import Image, ImageDraw, ImageFont
from io import BytesIO
from math import pi,cos,sin

BG='#10171e'; PANEL='#17262b'; EDGE='#385b57'; JADE='#65cdb0'; GOLD='#e9cc8b'; WHITE='#f1f3ee'; MUTED='#b0bdb8'

def font(n,bold=False):
 import os
 sources=['/usr/share/fonts/truetype/dejavu/DejaVuSans'+('-Bold' if bold else '')+'.ttf',
          'C:/Windows/Fonts/arial'+('bd' if bold else '')+'.ttf']
 for path in sources:
  if os.path.exists(path):return ImageFont.truetype(path,n)
 return ImageFont.load_default()

def render(data):
 im=Image.new('RGB',(1100,700),BG); d=ImageDraw.Draw(im)
 for x in range(0,1100,55):d.line((x,0,x+190,700),fill='#132028',width=1)
 d.rounded_rectangle((20,20,1080,680),radius=24,fill='#142027',outline=EDGE,width=3)
 d.text((53,44),'DAO OF ETERNITY',font=font(38,True),fill=GOLD)
 d.text((55,94),'THE LIVING DAO  /  ETERNAL CULTIVATION',font=font(17),fill=JADE)
 d.line((53,128,1046,128),fill=EDGE,width=2)
 d.text((55,152),str(data.get('realm','Cultivator')),font=font(26,True),fill=WHITE)
 d.text((55,193),str(data.get('identity','Unawakened')),font=font(18),fill=MUTED)
 d.rounded_rectangle((852,147,1039,190),radius=18,fill='#1c4539')
 d.text((871,158),'ETERNAL  ACTIVE',font=font(15,True),fill=JADE)
 center=(225,388);radius=125
 d.ellipse((center[0]-radius,center[1]-radius,center[0]+radius,center[1]+radius),outline=EDGE,width=22)
 portion=min(1,max(0,data.get('current',0)/max(1,data.get('cap',1))))
 d.arc((center[0]-radius,center[1]-radius,center[0]+radius,center[1]+radius),-90,-90+int(360*portion),fill=JADE,width=20)
 d.text((center[0],center[1]-48),'SPIRITUAL QI',font=font(16,True),anchor='mm',fill=MUTED)
 d.text((center[0],center[1]),f'{data.get("current",0):,} / {data.get("cap",0):,}',font=font(27,True),anchor='mm',fill=WHITE)
 d.text((center[0],center[1]+47),f'{portion*100:.1f}%  FILLED',font=font(17),anchor='mm',fill=GOLD)
 def tile(box,title,value,small=''):
  d.rounded_rectangle(box,radius=16,fill=PANEL,outline=EDGE,width=2)
  x,y,_,_=box;d.text((x+20,y+17),title,font=font(16,True),fill=MUTED)
  d.text((x+20,y+49),str(value),font=font(27,True),fill=GOLD)
  if small:d.text((x+20,y+89),str(small)[:45],font=font(14),fill=JADE)
 tile((420,236,715,357),'QI CIRCULATION',f'{data.get("rate",0):g} / hour',data.get('rate_note',''))
 tile((731,236,1045,357),'BREAKTHROUGH',data.get('eta','Ready')[:20])
 tile((420,374,715,494),'QI RESERVE',f'{data.get("bank",0):,}','Overflow bank')
 tile((731,374,1045,494),'LIFETIME QI',f'{data.get("earned",0):,}','Cultivation never expires')
 d.line((52,550,1044,550),fill=EDGE,width=2)
 d.text((62,570),'ACTIVE INFLUENCES',font=font(18,True),fill=GOLD)
 d.text((62,609),str(data.get('boosts','Root affinity & heavenly phenomena'))[:68],font=font(16),fill=WHITE)
 d.text((62,640),str(data.get('elixirs','No active elixir'))[:72],font=font(15),fill=JADE)
 output=BytesIO();im.save(output,format='PNG',optimize=True);output.seek(0);return output
