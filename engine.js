/* Browser counterpart of the Python engine. No network or credential access. */
'use strict';
const EcoEngine = (() => {
 const fields=['date','provider','service','region','resource_id','cost_usd','energy_kwh','intensity_g_per_kwh','pue'];
 const DAY=86400000;
 function num(value,name,min=0,max=1e12){
  if(value===null||value===undefined||String(value).trim()==='')throw Error(name+' is required');
  const n=Number(value);if(!Number.isFinite(n)||n<min||n>max)throw Error(name+' must be between '+min+' and '+max);return n;
 }
 function validDate(value){const str=String(value);if(!/^\d{4}-\d{2}-\d{2}$/.test(str))throw Error('Use YYYY-MM-DD dates');const stamp=Date.parse(str+'T00:00:00Z');if(!Number.isFinite(stamp)||new Date(stamp).toISOString().slice(0,10)!==str)throw Error('Invalid date');return str;}
 function validate(rows){
  if(!rows.length||rows.length>50000)throw Error('Import between 1 and 50,000 observations');const seen=new Set();
  return rows.map((row,index)=>{try{
   const r={};for(const f of fields){if(!(f in row))throw Error('Missing '+f);r[f]=row[f];}r.date=validDate(r.date);
   for(const f of fields.slice(1,5)){r[f]=String(r[f]).trim();if(!r[f]||r[f].length>200)throw Error(f+' must contain 1–200 characters');}
   r.provider=r.provider.toUpperCase();if(!['AWS','GCP','AZURE'].includes(r.provider))throw Error('provider must be AWS, GCP, or AZURE');
   for(const f of ['cost_usd','energy_kwh','intensity_g_per_kwh'])r[f]=num(r[f],f);r.pue=num(r.pue,'pue',1,5);
   const key=JSON.stringify(fields.slice(0,5).map(f=>r[f]));if(seen.has(key))throw Error('Duplicate daily resource');seen.add(key);return r;
  }catch(e){throw Error('CSV row '+(index+2)+': '+e.message);}});
 }
 function parseCSV(text){
  // Handles quoted commas, embedded newlines, escaped quotes, BOM and CRLF.
  text=text.replace(/^\uFEFF/,'');const rows=[];let row=[],cell='',quoted=false,closed=false;
  for(let i=0;i<text.length;i++){const c=text[i];
   if(quoted){if(c==='"'){if(text[i+1]==='"'){cell+='"';i++;}else{quoted=false;closed=true;}}else cell+=c;continue;}
   if(c==='"'){if(cell||closed)throw Error('Unexpected quote in CSV');quoted=true;continue;}
   if(c===','||c==='\n'||c==='\r'){
    row.push(cell);cell='';closed=false;
    if(c!==','){if(c==='\r'&&text[i+1]==='\n')i++;if(row.some(x=>x!==''))rows.push(row);row=[];}
   }else{if(closed)throw Error('Unexpected text after quoted field');cell+=c;}
  }
  if(quoted)throw Error('Unclosed CSV quote');if(cell||row.length||closed){row.push(cell);rows.push(row);}
  const header=rows.shift()||[];if(new Set(header).size!==header.length||!fields.every(f=>header.includes(f)))throw Error('Required columns: '+fields.join(','));
  return validate(rows.map((r,i)=>{if(r.length!==header.length)throw Error('CSV row '+(i+2)+': incorrect column count');return Object.fromEntries(header.map((f,j)=>[f,r[j]]));}));
 }
 function carbon(r){return r.energy_kwh*r.pue*r.intensity_g_per_kwh/1000;}
 function summarize(rows){
  const daily=new Map(),resources=new Map(),services=new Map();let cost=0,kg=0;
  for(const r of rows){const c=carbon(r);cost+=r.cost_usd;kg+=c;
   const defs=[[daily,r.date,{date:r.date}],[resources,JSON.stringify(fields.slice(1,5).map(f=>r[f])),Object.fromEntries(fields.slice(1,5).map(f=>[f,r[f]]))],[services,JSON.stringify([r.provider,r.service]),{provider:r.provider,service:r.service}]];
   for(const [map,key,meta]of defs){if(!map.has(key))map.set(key,{...meta,cost:0,carbon:0,energy:0});const item=map.get(key);item.cost+=r.cost_usd;item.carbon+=c;item.energy+=r.energy_kwh*r.pue;}
  }
  return {cost,carbon:kg,rows:rows.length,days:daily.size,timeline:[...daily.values()].sort((a,b)=>a.date.localeCompare(b.date)),resources:[...resources.values()].sort((a,b)=>b.cost-a.cost),services:[...services.values()]};
 }
 function simulate(rows,o={}){
  const share=num(o.share??100,'share',0,100)/100,cf=num(o.cost_factor??1,'cost_factor',0,3),ef=num(o.energy_factor??1,'energy_factor',0,3);
  const target=o.target_intensity==null?null:num(o.target_intensity,'target_intensity',0,2000);let affected=0;
  const projected=rows.map(r=>{const p={...r};if((!o.provider||o.provider==='ALL'||r.provider===o.provider)&&(!o.resource_id||o.resource_id==='ALL'||r.resource_id===o.resource_id)){
   affected++;p.cost_usd*=1-share+share*cf;const newKg=r.energy_kwh*r.pue*ef*(target??r.intensity_g_per_kwh)/1000;const mixed=carbon(r)*(1-share)+newKg*share;p.energy_kwh*=1-share+share*ef;p.intensity_g_per_kwh=p.energy_kwh?mixed*1000/(p.energy_kwh*p.pue):0;
  }return p;});return {current:summarize(rows),projected:summarize(projected),affected_rows:affected};
 }
 function median(values){const v=[...values].sort((a,b)=>a-b),m=Math.floor(v.length/2);return v.length%2?v[m]:(v[m-1]+v[m])/2;}
 function anomalies(rows){
  const daily=summarize(rows).timeline,lookup=new Map(daily.map(d=>[d.date,d])),flags=[];
  for(const item of daily){const stamp=Date.parse(item.date+'T00:00:00Z');const prior=[];for(let i=1;i<=14;i++){const day=new Date(stamp-i*DAY).toISOString().slice(0,10);if(lookup.has(day))prior.push(lookup.get(day));}if(prior.length<7)continue;
   for(const metric of ['cost','carbon']){const values=prior.map(p=>p[metric]),baseline=median(values),mad=median(values.map(v=>Math.abs(v-baseline))),threshold=baseline+Math.max(3*1.4826*mad,baseline*.25,.01);
    if(item[metric]>threshold)flags.push({date:item.date,metric,value:item[metric],baseline,threshold,resources:summarize(rows.filter(r=>r.date===item.date)).resources.sort((a,b)=>b[metric]-a[metric]).slice(0,5)});
   }
  }return flags;
 }
 function demoRows(){
  const specs=[['AWS','EC2','us-east-1','api-server',4.8,2.2,390],['AWS','RDS','us-east-1','production-db',6.2,3.5,390],['GCP','Compute Engine','europe-west1','worker-pool',3.8,2.9,180],['AZURE','Virtual Machines','eastus','analytics',5.5,3.1,420],['AWS','S3','us-west-2','archive',1.2,.4,160]];
  const rows=[],end=Date.parse(new Date().toISOString().slice(0,10)+'T00:00:00Z');
  for(let i=0;i<30;i++)specs.forEach(([provider,service,region,resource_id,cost,energy,intensity],j)=>{let f=1+.06*Math.sin(i+j);if(i===24&&resource_id==='analytics')f*=5;rows.push({date:new Date(end-(29-i)*DAY).toISOString().slice(0,10),provider,service,region,resource_id,cost_usd:Number((cost*f).toFixed(4)),energy_kwh:Number((energy*f).toFixed(4)),intensity_g_per_kwh:intensity,pue:1.2});});return rows;
 }
 function select(rows,o){let result=rows;const days=Number(o.days??30);if(![0,7,30,90,365].includes(days))throw Error('Invalid period');if(days&&result.length){const latest=result.reduce((a,r)=>r.date>a?r.date:a,result[0].date),cutoff=new Date(Date.parse(latest+'T00:00:00Z')-(days-1)*DAY).toISOString().slice(0,10);result=result.filter(r=>r.date>=cutoff);}if(o.filter_provider&&o.filter_provider!=='ALL')result=result.filter(r=>r.provider===o.filter_provider);if(o.date)result=result.filter(r=>r.date===validDate(o.date));return result;}
 function csv(rows){const quote=value=>'"'+String(value).replace(/"/g,'""')+'"';return fields.join(',')+'\r\n'+rows.map(r=>fields.map(f=>quote(r[f])).join(',')).join('\r\n');}
 return {fields,validate,parseCSV,carbon,summarize,simulate,anomalies,demoRows,select,csv};
})();
if(typeof module!=='undefined')module.exports=EcoEngine;
