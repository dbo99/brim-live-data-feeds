"""Missing/nonfinite fields must fail the actual JS numeric comparison helper."""
import json,subprocess,unittest
from pathlib import Path
class NumericComparisonTests(unittest.TestCase):
    def test_strict_js_numeric_comparison(self):
        helper=Path(__file__).with_name('window_parity.cjs')
        code='const {compare}=require('+json.dumps(str(helper))+''');
compare(3,3,'baseline');
for(const bad of [undefined,null,NaN,Infinity,'3']){
 let rejected=false;
 try{compare(3,bad,'deliberately missing or nonnumeric field');}catch(e){rejected=e.message.includes('missing/nonfinite/nonnumeric');}
 if(!rejected)throw Error('invalid actual was accepted: '+String(bad));
}
console.log('5 invalid actual values rejected; valid numeric baseline accepted');'''
        result=subprocess.run(['node','-e',code],capture_output=True,text=True)
        self.assertEqual(result.returncode,0,result.stdout+result.stderr)
if __name__=='__main__':unittest.main()
