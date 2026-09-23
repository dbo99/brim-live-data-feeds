// Strict parity comparison with the unchanged accepted UI core.
const fs=require('fs');
let checks=0,maxError=0;
function compare(e,a,label){if(typeof e==='number'){if(typeof a!=='number'||!Number.isFinite(e)||!Number.isFinite(a))throw Error(label+' missing/nonfinite/nonnumeric');const d=Math.abs(e-a);if(!Number.isFinite(d)||d>1e-9)throw Error(label);maxError=Math.max(maxError,d);}else if(e!==a)throw Error(label);checks++;}
function main(){
const [candidate,corePath,output]=process.argv.slice(2);const core=require(require('path').resolve(corePath));const index=JSON.parse(fs.readFileSync(candidate+'/docs/data/dendra/index.json'));

for(const s of index.streams){
 if(s.parameter!=='soil_moisture')continue;
 let rows=s.histories.flatMap(h=>JSON.parse(fs.readFileSync(candidate+'/'+h.path)).rows);
 for(const n of [7,14,30]){
  const expected=core.change(rows,index.complete_through_date,n),actual=s.change[n];
  for(const k of ['state','lag','required','span','delta'])compare(expected[k],actual[k],`${s.datastream_id} ${n} ${k}`);
  for(const w of ['recent','prior'])for(const k of ['start','end','n','expected','mean','maxGap'])compare(expected[w][k],actual[w]?.[k],`${s.datastream_id} ${n} ${w} ${k}`);
 }
}
if(checks===0)throw Error("No moisture window comparisons performed");
const result={passed:true,checks,max_absolute_error:maxError,comparison:'unchanged accepted ui/core.js versus R index'};fs.writeFileSync(output,JSON.stringify(result,null,2)+'\n');console.log(JSON.stringify(result));

}
module.exports={compare};
if(require.main===module)main();
