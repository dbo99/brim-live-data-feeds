"""Validate inactive workflow CLI flags without network, state or artifact writes."""
import re,subprocess,sys,unittest
from pathlib import Path
ROOT=Path(__file__).resolve().parents[2]
class CliContracts(unittest.TestCase):
    def test_python_cli_help_and_template_flags(self):
        text=(ROOT/'templates/build-dendra-daily.template.yml').read_text();count=0
        for line in text.splitlines():
            m=re.search(r'python3 (scripts/[\w/]+\.py) (discover|select|restore|prepare|validate)',line)
            if not m:continue
            result=subprocess.run([sys.executable,str(ROOT/m[1]),m[2],'--help'],capture_output=True,text=True)
            self.assertEqual(result.returncode,0,result.stderr)
            for flag in re.findall(r'--[a-z][a-z-]+',line):self.assertIn(flag,result.stdout)
            count+=1
        self.assertEqual(count,5)
    def test_r_cli_and_dependencies_declared(self):
        text=(ROOT/'templates/build-dendra-daily.template.yml').read_text();source=(ROOT/'scripts/build_dendra_daily.R').read_text()
        for line in text.splitlines():
            if 'scripts/build_dendra_daily.R ' in line:
                for flag in re.findall(r'--([a-z][a-z-]+)',line):
                    if flag!='vanilla':self.assertIn('"'+flag+'"',source)
        for package in ['jsonlite','digest']:self.assertIn('any::'+package,text)
        self.assertIn('python-version:',text)
if __name__=='__main__':unittest.main()
