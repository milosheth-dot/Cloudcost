'use strict';
const $ = id => document.getElementById(id);
const money = n => new Intl.NumberFormat('en-US',{style:'currency',currency:'USD'}).format(n);
const kg = n => n.toFixed(2)+' kgCO₂e';
let data, drill=null, loadVersion=0, scenarioVersion=0, timer;
async function api(url,body){
 const response=await fetch(url,body===undefined?{}:{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)});
 const result=await response.json(); if(!response.ok)throw Error(result.error||'Request failed');return result;
}
function selection(){return {dataset:$('dataset').value||'demo',days:Number($('days').value),filter_provider:$('provider').value};}
function query(){return new URLSearchParams(selection()).toString();}
function option(value,text){const e=document.createElement('option');e.value=value;e.textContent=text;return e;}
function node(tag,attrs={},text){const e=document.createElementNS('http://www.w3.org/2000/svg',tag);for(const [k,v]of Object.entries(attrs))e.setAttribute(k,v);if(text!==undefined)e.textContent=text;return e;}
function interactive(e,fn,label){e.setAttribute('tabindex','0');e.setAttribute('role','button');e.setAttribute('aria-label',label);e.addEventListener('click',fn);e.addEventListener('keydown',event=>{if(event.key==='Enter'||event.key===' '){event.preventDefault();fn();}});}
async function load(){
 const version=++loadVersion; ++scenarioVersion;
 try{
  const result=await api('/api/data?'+query());if(version!==loadVersion)return;
  data=result; const selected=$('dataset').value||'demo';
  $('dataset').replaceChildren(...data.datasets.map(d=>option(d.id,d.name)));$('dataset').value=selected;
  $('export').href='/api/export?'+query();
  const demo=selected==='demo';$('source').textContent=demo?'Synthetic demo · Illustrative energy and grid assumptions, not live cloud measurements.':'Imported observations · Carbon estimates depend on the energy, PUE and intensity you supplied.';
  const s=data.summary;$('totalCost').textContent=money(s.cost);$('totalCarbon').textContent=kg(s.carbon);$('resourceCount').textContent=s.resources.length;$('anomalyCount').textContent=data.anomalies.length;
  $('period').textContent=s.days+' observed days'+(s.days?' · through '+s.timeline.at(-1).date:'');
  $('resource').replaceChildren(option('ALL','All resources'),...Array.from(new Set(s.resources.map(r=>r.resource_id))).map(r=>option(r,r)));
  drill=null;$('dayDetails').textContent='';drawMap();drawTimeline();renderResources();renderAlerts();$('status').textContent='';await project();
 }catch(e){$('status').textContent=e.message;}
}
function drawMap(){
 const svg=$('map');svg.replaceChildren();$('back').hidden=!drill;
 const items=drill?data.summary.resources.filter(r=>r.provider===drill.provider&&r.service===drill.service):data.summary.services;
 const total=items.reduce((a,b)=>a+b.cost,0);let x=0;
 $('mapCaption').textContent=drill?drill.provider+' / '+drill.service:'All services · Hover or focus a block to inspect cost and estimated emissions.';
 if(!total){svg.append(node('text',{x:20,y:90},'No positive-cost observations in this selection.'));return;}
 const colors=['#37583f','#456844','#546c36','#2c6357','#5a5f36','#3f526c'];
 items.forEach((r,i)=>{const w=900*r.cost/total;const g=node('g',{class:'tile'});g.append(node('rect',{x:x+2,y:4,width:Math.max(0,w-4),height:198,rx:8,fill:colors[i%colors.length]}));
 const title=(r.resource_id||r.service)+' · '+r.provider+' · '+money(r.cost)+' · '+kg(r.carbon);g.append(node('title',{},title));
 if(w>80){g.append(node('text',{x:x+12,y:38},(r.resource_id||r.service).slice(0,Math.max(4,Math.floor(w/8)-2))));g.append(node('text',{x:x+12,y:65},money(r.cost)));if(w>120)g.append(node('text',{x:x+12,y:89},kg(r.carbon)));}
 interactive(g,()=>{if(r.resource_id){$('resource').value=r.resource_id;project();}else{drill=r;drawMap();}},title);svg.append(g);x+=w;});
}
async function showDay(day){
 try {const r=await api('/api/data?'+query()+'&date='+encodeURIComponent(day));
 $('dayDetails').textContent=day+' · '+money(r.summary.cost)+' · '+kg(r.summary.carbon)+' · Contributors: '+r.summary.resources.slice(0,5).map(x=>x.resource_id+' ('+money(x.cost)+', '+kg(x.carbon)+')').join(', ');
 }catch(e){$('status').textContent=e.message;}
}
function drawTimeline(){
 const svg=$('timeline');svg.replaceChildren();const values=data.summary.timeline,metric=$('metric').value;
 if(!values.length){svg.append(node('text',{x:20,y:100},'No observations.'));return;}
 const max=Math.max(.01,...values.map(r=>r[metric]))*1.15;
 const first=Date.parse(values[0].date),last=Date.parse(values.at(-1).date),span=Math.max(86400000,last-first);
 const X=r=>65+800*(Date.parse(r.date)-first)/span,Y=r=>210-175*r[metric]/max;
 for(let i=0;i<=4;i++){const y=210-i*175/4;svg.append(node('line',{x1:65,x2:865,y1:y,y2:y,class:'axis'}));svg.append(node('text',{x:3,y:y+4},(max*i/4).toFixed(1)));}
 // Separate segments when calendar days are missing, rather than implying measurements.
 let path='';values.forEach((r,i)=>{const gap=i&&Date.parse(r.date)-Date.parse(values[i-1].date)>86400000;path+=(i===0||gap?'M':'L')+X(r)+','+Y(r)+' ';});svg.append(node('path',{d:path,class:'chartline'}));
 values.forEach(r=>{const flag=data.anomalies.some(a=>a.date===r.date&&a.metric===metric);const c=node('circle',{cx:X(r),cy:Y(r),r:flag?6:4,class:'point'+(flag?' flag':'')});const label=r.date+' · '+(metric==='cost'?money(r.cost):kg(r.carbon));c.append(node('title',{},label));interactive(c,()=>showDay(r.date),label);svg.append(c);});
 svg.append(node('text',{x:65,y:240},values[0].date));svg.append(node('text',{x:790,y:240},values.at(-1).date));
}
function renderResources(){const body=$('resources');body.replaceChildren();data.summary.resources.slice(0,10).forEach(r=>{const tr=document.createElement('tr'),name=document.createElement('td');name.textContent=r.resource_id;const meta=document.createElement('small');meta.textContent=r.provider+' / '+r.service+' / '+r.region;name.append(meta);tr.append(name);for(const value of [money(r.cost),r.carbon.toFixed(2)]){const td=document.createElement('td');td.textContent=value;tr.append(td);}const td=document.createElement('td'),b=document.createElement('button');b.textContent='Simulate';b.className='secondary';b.onclick=()=>{$('resource').value=r.resource_id;project();};td.append(b);tr.append(td);body.append(tr);});}
function renderAlerts(){const root=$('alerts');root.replaceChildren();if(!data.anomalies.length){root.textContent='No spikes detected. At least 7 prior observed days within 14 calendar days are required.';return;}data.anomalies.slice().reverse().forEach(a=>{const div=document.createElement('div');div.className='alert';const b=document.createElement('button');b.className='secondary';b.textContent=a.date+' · '+a.metric+' spike · '+(a.metric==='cost'?money(a.value):kg(a.value));b.onclick=()=>showDay(a.date);div.append(b);root.append(div);});}
function delta(current, projected){if(!current)return projected===0?'No change':'Change from zero baseline';const pct=(projected/current-1)*100;return (pct>0?'+':'')+pct.toFixed(1)+'% vs observed period';}
async function project(){
 if(!data)return;const version=++scenarioVersion;const share=Number($('share').value),cost=Number($('cost').value)/100,energy=Number($('energy').value)/100;
 $('shareOut').textContent=share+'%';$('costOut').textContent=cost.toFixed(2)+'×';$('energyOut').textContent=energy.toFixed(2)+'×';
 const input=$('intensity');if(!input.checkValidity()){input.reportValidity();return;}
 try{const r=await api('/api/simulate',{...selection(),resource_id:$('resource').value,share,cost_factor:cost,energy_factor:energy,target_intensity:input.value===''?null:Number(input.value)});if(version!==scenarioVersion)return;
 $('projectedCost').textContent=money(r.projected.cost);$('projectedCarbon').textContent=kg(r.projected.carbon);$('costDelta').textContent=delta(r.current.cost,r.projected.cost);$('carbonDelta').textContent=delta(r.current.carbon,r.projected.carbon);$('affected').textContent=r.affected_rows+' daily observations in scenario · '+share+'% of selected workload';$('status').textContent='';
 }catch(e){if(version===scenarioVersion)$('status').textContent=e.message;}
}
for(const id of ['dataset','provider','days'])$(id).addEventListener('change',load);
for(const id of ['resource','share','cost','energy','intensity'])$(id).addEventListener('input',()=>{++scenarioVersion;clearTimeout(timer);timer=setTimeout(project,120);});
$('refresh').onclick=load;$('metric').onchange=()=>data&&drawTimeline();$('back').onclick=()=>{drill=null;drawMap();};
$('reset').onclick=()=>{$('resource').value='ALL';$('share').value=100;$('cost').value=100;$('energy').value=100;$('intensity').value='';project();};
$('importButton').onclick=()=>$('file').click();$('file').onchange=async()=>{const file=$('file').files[0];if(!file)return;try{if(file.size>9000000)throw Error('CSV file limit: 9 MB');const result=await api('/api/import',{name:file.name,csv:await file.text()});$('dataset').append(option(result.dataset,file.name));$('dataset').value=result.dataset;await load();}catch(e){$('status').textContent=e.message;}finally{$('file').value='';}};
load();
