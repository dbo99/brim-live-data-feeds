"""Real R semantic stage and real pinned shared preparation; no boundary double."""
import copy,csv,json,shutil,subprocess,sys,tempfile,unittest
from pathlib import Path
import test_safeguards as fixtures
ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT/'scripts'))
import dendra_candidate as adapter

class SemanticTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.fixture=fixtures.SafeguardTests();cls.fixture.setUp()
        cls.baseline=Path(json.loads(cls.fixture.prior)['candidate_root'])
    @classmethod
    def tearDownClass(cls):cls.fixture.tearDown()
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.root=Path(self.tmp.name)/'candidate';shutil.copytree(self.baseline,self.root)
        self.index=adapter.load(self.root/adapter.FIXED[0]);self.s=self.index['streams'][0];self.p=adapter.load(self.root/self.s['diagnostics_path'])
    def tearDown(self):self.tmp.cleanup()
    def flush(self,project=False):
        adapter.write(self.root/self.s['diagnostics_path'],self.p)
        if project:
            for h in self.s['histories']:
                hp=adapter.load(self.root/h['path']);hp['rows']=[adapter.projected(r) for r in self.p['rows'] if r['water_year']==h['water_year']];adapter.write(self.root/h['path'],hp)
            with (self.root/self.s['csv_path']).open('w',newline='') as f:
                w=csv.DictWriter(f,fieldnames=adapter.CSV_FIELDS);w.writeheader();w.writerows(adapter.csv_record(self.s['datastream_id'],r) for r in self.p['rows'])
        for s in self.index['streams']:
            for h in s['histories']:h['sha256']=adapter.sha(self.root/h['path'])
        for f in self.index['files']:f.update(sha256=adapter.sha(self.root/f['path']),bytes=(self.root/f['path']).stat().st_size)
        adapter.write(self.root/adapter.FIXED[0],self.index)
    def reject(self,pattern):
        for action in (lambda:adapter.validate(self.root),lambda:adapter.prepare(self.root,'a'*40)):
            with self.assertRaisesRegex(ValueError,pattern):action()
        self.assertFalse((self.root.parent/'candidate-metadata.json').exists())
    def test_baseline_real_prepare(self):
        self.assertEqual(adapter.validate(self.root)['semantic']['status'],'passed')
        metadata=adapter.prepare(self.root,'a'*40)
        adapter.shared.validate_candidate_metadata(root=self.root,metadata_path=metadata,product_id=adapter.PRODUCT,allowlist=adapter.FIXED,owned_roots=adapter.OWNED,expected_source_sha='a'*40)
    def test_contradictory_change(self):
        self.s['change']['7'].update(delta=-10,state='drying');self.flush();self.reject('change')
    def test_conversion_identity(self):
        self.s['unit_normalization']['multiplier']=100;self.flush();self.reject('unit_normalization')
    def test_disqualifying_range(self):
        r=next(r for r in self.p['rows'] if r['plot_eligible']);r['n_out_of_range']=1;r['flags'].append('out_of_range_values');self.flush(project=True);self.reject('plot_eligible')
    def test_expiry_not_derived(self):
        self.index['expires_at_utc']='2099-01-01T00:00:00Z';self.flush();self.reject('expires_at_utc|expiry')
    def test_fabricated_unknown_scale(self):
        for obj in (self.s,self.p['stream']):
            obj['native_unit_name']='Dimensionless';obj['source_terms']['dt']['Unit']='Dimensionless';obj['unit_normalization']['unit_definition']['label']='Dimensionless'
        for h in self.s['histories']:
            hp=adapter.load(self.root/h['path']);hp.update(native_unit_name='Dimensionless',unit_normalization=self.s['unit_normalization']);adapter.write(self.root/h['path'],hp)
        self.flush();self.reject('unsupported unit')
    def test_coverage_vs_samples(self):
        r=next(r for r in self.p['rows'] if r['plot_eligible']);r['expected_samples']=288;self.flush(project=True);self.reject('expected_samples')
    def test_native_conversion_relationship(self):
        r=next(r for r in self.p['rows'] if r['plot_eligible']);r['mean_native']=25;self.flush(project=True);self.reject('mean_value')
    def test_missing_summary_number(self):
        del self.s['change']['7']['recent']['mean'];self.flush();self.reject('change')
    def test_nonfinite_summary_number(self):
        self.s['change']['7']['delta']='NaN';self.flush();self.reject('change')
    def test_latest_summary(self):
        self.s['summary']['last_plottable_date']='2026-09-18';self.flush();self.reject('summary')
    def test_lag_and_gap_summary(self):
        self.s['change']['7']['lag']=2;self.s['change']['7']['recent']['maxGap']=1;self.flush();self.reject('change')
    def test_policy_versions(self):
        self.p['aggregation']['policy_version']='guessed';self.flush();self.reject('policy_version')
    def test_history_unit(self):
        h=self.s['histories'][0];hp=adapter.load(self.root/h['path']);hp['unit']='Kelvin';adapter.write(self.root/h['path'],hp);self.flush();self.reject('target unit')
    def test_orientation_identity(self):
        self.s['orientation']='vertical';self.flush();self.reject('orientation')
    def test_csv_nonaccepted_field(self):
        cp=self.root/self.s['csv_path']
        with cp.open(newline='') as f:r=list(csv.DictReader(f))
        r[-1]['flags']='out_of_range_values'
        with cp.open('w',newline='') as f:w=csv.DictWriter(f,fieldnames=adapter.CSV_FIELDS);w.writeheader();w.writerows(r)
        self.flush();self.reject('CSV flags')
    def test_future_build(self):
        self.index['generated_at_utc']='2099-01-01T00:00:00Z';self.flush();self.reject('future')
    def test_future_retrieval(self):
        self.p['source_snapshot']['chunks'][-1]['retrieval_last_utc']='2099-01-01T00:00:00Z';self.flush();self.reject('retrieval after build')
    def test_projection_missing_number(self):
        h=self.s['histories'][0];hp=adapter.load(self.root/h['path']);del hp['rows'][-1]['coverage'];adapter.write(self.root/h['path'],hp);self.flush();self.reject('Projection')
    def test_cadence_source(self):
        self.p['rows'][0]['cadence_source']='guessed';self.flush();self.reject('cadence_source')
    def test_cadence_context_seconds(self):
        self.s['cadence_context']['seconds']=-600;self.p['cadence_context']['seconds']=-600;self.flush();self.reject('cadence')
    def test_cadence_fallback_agreement(self):
        r=self.p['rows'][0];r['cadence_seconds']=3600;r['expected_samples']=24;self.flush(project=True);self.reject('fallback')
    def test_water_year_length(self):
        self.p['rows'][0]['water_year_days']=1;self.flush();self.reject('water_year_days')
    def test_modern_run_clock_required(self):
        del self.index['run_started_at_utc'];self.flush();self.reject('run_started_at_utc')
    def test_modern_live_metadata_clock_required(self):
        self.index['mode']='live';self.index['expires_at_utc']='2026-09-23T12:00:00Z'
        del self.index['metadata_verified_at_utc'];self.flush();self.reject('metadata_verified_at_utc')

class ParameterSemanticsTests(unittest.TestCase):
    def test_unresolved_and_valid_negative_temperature(self):
        # Both cases are rebuilt from native fixture values by R, then real prepare.
        for parameter,unit,normalization in [
            ('soil_moisture','Dimensionless',dict(status='unresolved',multiplier=None,offset=None)),
            ('soil_temperature','DegreeCelsius',dict(status='verified_temperature_conversion',multiplier=1,offset=0,target_unit='degree Celsius',unit_definition=dict(label='DegreeCelsius'))),
            ('soil_temperature','DegreeFahrenheit',dict(status='verified_temperature_conversion',multiplier=5/9,offset=-32*5/9,target_unit='degree Celsius',unit_definition=dict(label='DegreeFahrenheit')))]:
            with self.subTest(unit=unit):
                fixture=fixtures.SafeguardTests();fixture.setUp()
                try:
                    s=copy.deepcopy(fixture.streams[0]);s.update(parameter=parameter,native_unit_name=unit,unit_normalization=normalization)
                    s['source_terms']['dt']['Unit']=unit;s['source_terms']['ds']['Variable']='Temperature' if parameter=='soil_temperature' else 'VolumetricWaterContent'
                    fixture.streams=[s];fixture.catalog['streams']=[s];fixture.native('2026-08-21','2026-09-20')
                    manifest=adapter.load(fixture.p/'native.json');p=Path(manifest['streams'][0]['native_csv'])
                    with p.open(newline='') as f:rows=list(csv.DictReader(f));fields=list(rows[0])
                    for r in rows:r['v']='14' if unit=='DegreeFahrenheit' else '-5'
                    with p.open('w',newline='') as f:w=csv.DictWriter(f,fieldnames=fields);w.writeheader();w.writerows(rows)
                    manifest['streams'][0]['native_sha256']=adapter.sha(p);fixtures.write(fixture.p/'native.json',manifest)
                    state=fixture.p/'parameter-state'
                    fixture.invoke('replay',days=30,extra={'state':str(state),'start':'2026-08-21'})
                    pointer=adapter.load(state/'current.json');root=Path(pointer['candidate_root']);adapter.prepare(root,'a'*40)
                    index=adapter.load(root/adapter.FIXED[0]);diagnostic=adapter.load(root/index['streams'][0]['diagnostics_path'])
                    for r in diagnostic['rows']:
                        self.assertEqual(r['plot_eligible'],parameter=='soil_temperature')
                        self.assertAlmostEqual(r['mean_value'],-10 if unit=='DegreeFahrenheit' else -5)
                        self.assertIsNone(r['mean_percent'])
                finally:fixture.tearDown()

if __name__=='__main__':unittest.main()
