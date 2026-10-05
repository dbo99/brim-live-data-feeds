"""Offline daily adapter regressions; original sources are never written."""
import csv
from datetime import datetime,timedelta,timezone
import hashlib
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest

ROOT=Path(__file__).resolve().parents[2]
spec=importlib.util.spec_from_file_location('daily',ROOT/'scripts/dendra/bulk_daily.py')
m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)


class DailyTests(unittest.TestCase):
    def test_api_subseconds_cannot_alias_export_seconds(self):
        a=m.exact_api_local('2024-02-29T08:00:00.001Z')
        b=m.exact_api_local('2024-02-29T08:00:00Z')
        self.assertEqual(a,'2024-02-29 00:00:00.001000')
        self.assertEqual(b,'2024-02-29 00:00:00')
        native=dict(rows=1,sha256=hashlib.sha256(m.canonical_pair(b,20)).hexdigest())
        api=dict(rows=1,sha256=hashlib.sha256(m.canonical_pair(a,20)).hexdigest(),quarantined=0,nonnumeric=0,conflicts=0)
        intervals=[(m.utc('2024-02-29T08:00:00Z'),m.utc('2024-03-01T08:00:00Z'))]
        self.assertEqual(m.quality_decision('2024-02-29',native,api,intervals)['state'],'UNRESOLVED')

    def test_daily_parquet_nullable_numeric_schema_and_determinism(self):
        with tempfile.TemporaryDirectory() as tmp:
            p=Path(tmp);helper=m.bulk.compile_helper(p/'arrow-helper')
            rows=[dict(stream_id='one',date='2024-03-01',daily_mean_vwc_percent=None,depth_cm=None,n_valid=0,plot_eligible=False)]
            m.daily_table(helper,p/'a.csv',rows);m.daily_table(helper,p/'b.csv',rows)
            self.assertEqual(m.bulk.sha(p/'a.parquet'),m.bulk.sha(p/'b.parquet'))
            schema=m.bulk.run(['parquet-dump-schema',p/'a.parquet']).stdout
            self.assertRegex(schema,r'optional double .* daily_mean_vwc_percent;')
            self.assertRegex(schema,r'optional double .* depth_cm;')

    def test_quality_unknown_is_not_absent(self):
        intervals=[(m.utc('2024-02-29T08:00:00Z'),m.utc('2024-03-01T08:00:00Z'))]
        native=dict(rows=144,sha256='same')
        clear=dict(native,quarantined=0,nonnumeric=0,conflicts=0)
        self.assertEqual(m.quality_decision('2024-02-29',native,clear,intervals)['state'],'RESOLVED_CLEAR')
        self.assertEqual(m.quality_decision('2024-02-29',native,None,intervals)['state'],'UNRESOLVED')
        self.assertEqual(m.quality_decision('2024-02-29',native,dict(clear,quarantined=1),intervals)['state'],'QUARANTINED')
        for bad in [dict(clear,sha256='different'),dict(clear,nonnumeric=1),dict(clear,conflicts=1)]:
            self.assertEqual(m.quality_decision('2024-02-29',native,bad,intervals)['state'],'UNRESOLVED')
        self.assertEqual(m.quality_decision('2024-02-29',native,clear,[])['state'],'UNRESOLVED')

    def test_complete_days_across_adjacent_seals(self):
        refs=[dict(start='2024-02-29T08:00:00Z',end='2024-02-29T20:00:00Z'),dict(start='2024-02-29T20:00:00Z',end='2024-03-01T08:00:00Z')]
        self.assertTrue(m.complete_day('2024-02-29',m.interval_union(refs)))
        refs[1]['start']='2024-02-29T20:00:01Z'
        self.assertFalse(m.complete_day('2024-02-29',m.interval_union(refs)))
        self.assertEqual(m.naive('2024-07-01T08:00:00Z'),'2024-07-01 00:00:00')

    def test_quality_classifier_preserves_existing_veto(self):
        self.assertFalse(m.quality.classify({'v':0})['quarantined'])
        self.assertTrue(m.quality.classify({'v':0,'q':False})['quarantined'])
        self.assertFalse(m.quality.classify({'v':0,'q':None})['quarantined'])

    def test_timestamp_probe_selection_bounded(self):
        rows=[];refs={}
        for i in range(12):
            sid=str(i);rows.append(dict(proposed_stream_id=sid,materialized='True',proposed_organization='family',
                minimum='0',maximum='1',depth_cm='',relative_asset_path='file'+str(i%2)))
            refs[sid]=[{}]
        selected=m.select_timestamp_samples(rows,refs)
        self.assertEqual(len(selected),3)
        self.assertEqual(len({r['relative_asset_path'] for r in selected}),2)

    def science(self,p,multiplier=1,unit='Percent'):
        sid='0'*24;inp=p/'native.csv'
        with inp.open('w',newline='') as f:
            w=csv.writer(f);w.writerow(['datastream_id','t','v','value_status','duplicate_conflict','alternative_out_of_range'])
            for day in [28,29]:
                for i in range(144):
                    t=datetime(2024,2,day,8)+timedelta(minutes=10*i)
                    w.writerow([sid,t.strftime('%Y-%m-%dT%H:%M:%SZ'),0 if day==28 else .2,'number',False,False])
            # One observation cannot become an accepted day.
            w.writerow([sid,'2024-03-01T08:00:00Z',.5,'number',False,False])
            for i in range(144):
                t=datetime(2024,3,2,8)+timedelta(minutes=10*i)
                w.writerow([sid,t.strftime('%Y-%m-%dT%H:%M:%SZ'),-235 if i==0 else .2,'number',False,False])
            for i in range(144):
                t=datetime(2024,3,3,8)+timedelta(minutes=10*i)
                w.writerow([sid,t.strftime('%Y-%m-%dT%H:%M:%SZ'),7999 if i==0 else .2,'number',False,False])
        cfg=dict(version='dendra-bulk-daily-input-2',core_sha256=m.bulk.sha(ROOT/'scripts/dendra/core.R'),csv=str(inp),csv_sha256=m.bulk.sha(inp),
            stream_id=sid,native_unit=unit,multiplier=multiplier,start='2024-02-28',end='2024-03-05',as_of='2024-03-05T12:00:00Z',
            quality_days={d:dict(state='RESOLVED_CLEAR',reason='test',query_complete=True) for d in ['2024-02-28','2024-02-29','2024-03-01','2024-03-02','2024-03-03']})
        return inp,cfg

    def test_r_science_screens_leap_zero_missing_and_determinism(self):
        with tempfile.TemporaryDirectory() as tmp:
            p=Path(tmp);inp,cfg=self.science(p,100,'VolumetricWaterContent');original=m.bulk.sha(inp)
            m.bulk.write_json(p/'input.json',cfg)
            for name in ['a','b']:m.bulk.run(['Rscript','--vanilla',ROOT/'scripts/dendra/bulk_daily.R',p/'input.json',p/(name+'.json')])
            self.assertEqual((p/'a.json').read_bytes(),(p/'b.json').read_bytes())
            rows=json.loads((p/'a.json').read_text())['rows']
            self.assertEqual(rows[0]['daily_mean_vwc_percent'],0)
            self.assertEqual(rows[1]['daily_mean_vwc_percent'],20)
            self.assertEqual(rows[1]['dowy'],152);self.assertEqual(rows[1]['water_day_aligned'],152)
            contract=m.contract_columns(rows[1])
            self.assertEqual(contract['date_pst_fixed'],'2024-02-29')
            self.assertEqual(contract['plot_day_aligned'],152)
            self.assertEqual(contract['valid_sample_count'],144)
            self.assertEqual(contract['expected_sample_count'],144)
            self.assertEqual(contract['sample_count_fraction'],1)
            self.assertEqual([r['daily_status'] for r in rows],['ACCEPTED','ACCEPTED','WITHHELD_BY_EXISTING_SCREEN','WITHHELD_BY_EXISTING_SCREEN','WITHHELD_BY_EXISTING_SCREEN','MISSING'])
            self.assertTrue(all(r['daily_mean_vwc_percent'] is None for r in rows[2:]))
            self.assertEqual(original,m.bulk.sha(inp))
            for r in rows:r['stream_id']='0'*24
            m.validate_daily(rows,'0'*24)
            with self.assertRaisesRegex(ValueError,'duplicate'):m.validate_daily(rows+[rows[0]],'0'*24)

    def test_r_unknown_and_provider_quality_have_no_mean(self):
        with tempfile.TemporaryDirectory() as tmp:
            p=Path(tmp);_,cfg=self.science(p)
            cfg['quality_days']['2024-02-28']['state']='QUARANTINED'
            cfg['quality_days']['2024-02-29']['state']='UNRESOLVED'
            m.bulk.write_json(p/'input.json',cfg)
            m.bulk.run(['Rscript','--vanilla',ROOT/'scripts/dendra/bulk_daily.R',p/'input.json',p/'out.json'])
            rows=json.loads((p/'out.json').read_text())['rows']
            self.assertEqual(rows[0]['daily_status'],'WITHHELD_BY_EXISTING_SCREEN')
            self.assertEqual(rows[1]['daily_status'],'UNRESOLVED_SEMANTICS')
            self.assertEqual(rows[0]['n_valid'],0);self.assertIsNone(rows[1]['daily_mean_vwc_percent'])

    def test_unknown_depth_fixture_keeps_exact_stream_identity(self):
        with tempfile.TemporaryDirectory() as tmp:
            base=dict(depth_cm='',depth_status='UNRESOLVED',product_class=m.bulk.CLASSES[0],proposed_station='S',proposed_station_id='station',
                proposed_stream_id='one',export_local_series_key='key1',orientation='vertical',equipment_type_id='',native_unit='Percent',
                conversion_multiplier='1',proposed_organization='org',file_sha256='source',native_asset='one.parquet',asset_id='one')
            other=dict(base,proposed_stream_id='two',export_local_series_key='key2')
            f=m.consumer_fixture([(base,[dict(daily_status='ACCEPTED',daily_mean_vwc_percent=0)]),(other,[dict(daily_status='ACCEPTED',daily_mean_vwc_percent=1)])],Path(tmp))
            x=f['examples']['unknown_depth'];self.assertIsNone(x['depth_cm']);self.assertEqual(x['stream_id'],'one')
            self.assertEqual(x['export_local_series_key'],'key1')


class BulkAdmissionTests(unittest.TestCase):
    def row(self):
        return dict(file_sha256='a'*64,column_ordinal_1_based=2,
            export_local_series_key='csv-sha256:'+'a'*64+':column:2',
            mapping_state='CORROBORATED_PROPOSAL',product_class=m.bulk.CLASSES[0],
            native_unit='Percent',conversion_multiplier='1',observation_count='144',
            duplicate_of='',depth_cm='',proposed_stream_id='0'*24)

    def source(self):
        return dict(identity_ready=True,provider_purpose='ReadytoUse',source_suitable=True)

    def admission(self,row=None,source=None,time='CORROBORATED_FIXED_UTC_MINUS_08'):
        return m.historical_bulk_admission(row or self.row(),time,source or self.source())

    def test_ready_to_use_missing_q_enters_without_a_good_quality_claim(self):
        admission=self.admission()
        self.assertTrue(admission['admitted'])
        self.assertEqual(admission['quality_admission_basis'],'PROVIDER_READY_TO_USE')
        self.assertEqual(admission['observation_api_q'],'UNAVAILABLE')
        decision=m.bulk_quality_decision('2024-02-29',dict(rows=144,sha256='native'),None,[])
        self.assertEqual(decision['state'],'PROVIDER_READY_TO_USE')
        self.assertIn('UNAVAILABLE',decision['reason'])
        self.assertNotEqual(decision['state'],'RESOLVED_CLEAR')
        self.assertFalse(decision['query_complete'])
        self.assertNotIn('q',decision)

    def test_raw_status_unknown_and_conflicting_purpose_stay_held(self):
        for purpose in ['Raw','StatusInformation',None,'CONFLICTING']:
            with self.subTest(purpose=purpose):
                decision=self.admission(source=dict(self.source(),provider_purpose=purpose))
                self.assertFalse(decision['admitted'])
                self.assertIn('PROVIDER_PURPOSE_NOT_READYTOUSE',decision['holds'])

    def test_unresolved_time_unit_identity_and_configuration_stay_held(self):
        cases=[(self.row(),self.source(),'UNRESOLVED','TIMESTAMP_UNRESOLVED'),
               (dict(self.row(),product_class=m.bulk.CLASSES[2],native_unit='Dimensionless',conversion_multiplier=''),
                self.source(),'ACCEPTED_FIXED_UTC_MINUS_08','UNIT_OR_TARGET_UNRESOLVED'),
               (dict(self.row(),mapping_state='AMBIGUOUS'),dict(self.source(),identity_ready=False),
                'ACCEPTED_FIXED_UTC_MINUS_08','IDENTITY_UNRESOLVED'),
               (self.row(),dict(self.source(),source_suitable=False),'ACCEPTED_FIXED_UTC_MINUS_08',
                'SOURCE_CONFIGURATION_UNSUITABLE')]
        for row,source,time,hold in cases:
            with self.subTest(hold=hold):
                decision=self.admission(row,source,time);self.assertFalse(decision['admitted'])
                self.assertIn(hold,decision['holds'])

    def test_exact_export_identity_and_resolved_conversion_required(self):
        self.assertFalse(self.admission(dict(self.row(),export_local_series_key='another-column'))['admitted'])
        self.assertFalse(self.admission(dict(self.row(),conversion_multiplier='100'))['admitted'])

    def test_unknown_depth_allowed_without_nearest_depth_substitution(self):
        row=self.row();before=dict(row)
        self.assertTrue(self.admission(row)['admitted'])
        self.assertEqual(row,before);self.assertEqual(row['depth_cm'],'')

    def test_all_null_catalog_selection_has_no_screened_observations(self):
        decision=self.admission(dict(self.row(),observation_count='0'))
        self.assertFalse(decision['admitted'])
        self.assertIn('ALL_NULL_TARGET_NO_OBSERVATIONS',decision['holds'])

    def test_api_native_route_and_quality_policy_are_not_weakened(self):
        decision=m.historical_bulk_admission(self.row(),'ACCEPTED_FIXED_UTC_MINUS_08',self.source(),route='API_NATIVE')
        self.assertFalse(decision['admitted'])
        self.assertEqual(m.quality_decision('2024-02-29',dict(rows=144,sha256='native'),None,[])['state'],'UNRESOLVED')
        self.assertTrue(m.quality.classify({'v':20,'q':False})['quarantined'])

    def test_known_quality_and_observation_set_vetoes_survive_bulk_admission(self):
        intervals=[(m.utc('2024-02-29T08:00:00Z'),m.utc('2024-03-01T08:00:00Z'))]
        native=dict(rows=144,sha256='native')
        api=dict(rows=144,sha256='different',quarantined=0,nonnumeric=0,conflicts=0)
        self.assertEqual(m.bulk_quality_decision('2024-02-29',native,api,intervals)['reason'],'CSV_API_OBSERVATION_SET_DIFFERS')
        self.assertEqual(m.bulk_quality_decision('2024-02-29',native,dict(api,quarantined=1),[])['state'],'QUARANTINED')
        self.assertEqual(m.bulk_quality_decision('2024-02-29',native,None,[],source_veto=True)['state'],'QUARANTINED')
        self.assertEqual(m.bulk_quality_decision('2024-02-29',native,dict(api,conflicts=1),[])['state'],'UNRESOLVED')

    def test_r2_protected_rows_and_only_quality_hold_transitions(self):
        protected=dict(date='2024-02-29',daily_status='ACCEPTED',daily_mean_vwc_percent=20,
                       source_quality_status='RESOLVED_CLEAR',source_quality_reason='EXACT_DAY_MATCH_NO_PROVIDER_QUALITY_VETO')
        self.assertEqual(m.compare_r2_science([protected],[protected])['protected_rows_unchanged'],1)
        with self.assertRaisesRegex(ValueError,'protected'):
            m.compare_r2_science([dict(protected,daily_mean_vwc_percent=21)],[protected])
        old=dict(protected,daily_status='UNRESOLVED_SEMANTICS',daily_mean_vwc_percent=None,
                 source_quality_status='UNRESOLVED',source_quality_reason='NO_COMPLETE_RETAINED_API_DAY')
        changed=dict(protected,source_quality_status='PROVIDER_READY_TO_USE',
                     source_quality_reason='API_Q_UNAVAILABLE_BULK_READYTOUSE')
        self.assertEqual(m.compare_r2_science([changed],[old])['intended_transitions'],{'UNRESOLVED_SEMANTICS->ACCEPTED':1})
        with self.assertRaisesRegex(ValueError,'protected'):
            m.compare_r2_science([changed],[dict(old,source_quality_reason='CSV_API_OBSERVATION_SET_DIFFERS')])
        for state in ['MISSING','WITHHELD_BY_EXISTING_SCREEN']:
            before=dict(old,daily_status=state)
            with self.assertRaisesRegex(ValueError,'protected'):m.compare_r2_science([changed],[before])

    def test_bulk_r_screen_preserves_negative_values_and_missing_days(self):
        helper=DailyTests()
        with tempfile.TemporaryDirectory() as tmp:
            p=Path(tmp);inp,cfg=helper.science(p,100,'VolumetricWaterContent')
            # Include the actual Pepperwood negative value in a complete day.
            body=inp.read_text().replace('-235','-8.191');inp.write_text(body);pin=m.bulk.sha(inp)
            cfg.update(version='dendra-bulk-daily-input-3',historical_bulk_policy=m.BULK_POLICY,
                       historical_source_route=m.BULK_ROUTE,quality_admission_basis=m.BULK_BASIS,csv_sha256=pin)
            for decision in cfg['quality_days'].values():decision.update(state=m.BULK_BASIS,reason='API_Q_UNAVAILABLE_BULK_READYTOUSE',query_complete=False)
            cfg['quality_days']['2024-03-04']=dict(state=m.BULK_BASIS,reason='API_Q_UNAVAILABLE_BULK_READYTOUSE',query_complete=False)
            m.bulk.write_json(p/'input.json',cfg)
            m.bulk.run(['Rscript','--vanilla',ROOT/'scripts/dendra/bulk_daily.R',p/'input.json',p/'out.json'])
            rows=json.loads((p/'out.json').read_text())['rows']
            self.assertEqual(rows[0]['daily_mean_vwc_percent'],0)
            self.assertEqual(rows[1]['daily_mean_vwc_percent'],20)
            self.assertEqual(rows[3]['daily_status'],'WITHHELD_BY_EXISTING_SCREEN')
            self.assertEqual(rows[3]['n_out_of_range'],1)
            self.assertEqual(rows[-1]['daily_status'],'MISSING')
            self.assertFalse(rows[-1]['source_empty']);self.assertFalse(rows[-1]['query_complete'])
            self.assertEqual(m.bulk.sha(inp),pin)
            self.assertIn('-8.191',inp.read_text())

    def test_r2_csv_number_spelling_survives_protected_row_reuse(self):
        properties=dict(date=dict(type='string'),daily_mean_vwc_percent=dict(type=['number','null']),
                        expected_samples=dict(type=['number','null']))
        schema=dict(properties=dict(examples=dict(properties=dict(unknown_depth=dict(
            properties=dict(records=dict(items=dict(properties=properties))))))))
        with tempfile.TemporaryDirectory() as tmp:
            p=Path(tmp);source=p/'r2.csv';output=p/'r3.csv'
            m.bulk.write_csv(source,[dict(date='2024-02-29',daily_mean_vwc_percent=0,expected_samples=144),
                                     dict(date='2024-03-01',daily_mean_vwc_percent=None,expected_samples=144.0)])
            rows=m.read_daily_csv(source,schema)
            m.bulk.write_csv(output,rows)
            self.assertEqual(source.read_bytes(),output.read_bytes())

    def test_api_r_input_cannot_use_the_bulk_quality_state(self):
        helper=DailyTests()
        with tempfile.TemporaryDirectory() as tmp:
            p=Path(tmp);_,cfg=helper.science(p)
            cfg['quality_days']['2024-02-29']['state']=m.BULK_BASIS
            m.bulk.write_json(p/'input.json',cfg)
            with self.assertRaises(m.subprocess.CalledProcessError):
                m.bulk.run(['Rscript','--vanilla',ROOT/'scripts/dendra/bulk_daily.R',p/'input.json',p/'out.json'])

    def test_candidate_2_consumer_schema_bytes_remain_exact(self):
        with tempfile.TemporaryDirectory() as tmp:
            m.consumer_fixture([],Path(tmp))
            self.assertEqual(m.bulk.sha(Path(tmp)/'00G_CANDIDATE_SCHEMA.json'),
                             '2e3a9ddcbc3c56cb4bd633fa2b3fcadb78e255bfef9b7fb907a6793610342c3d')


class BoundedSeriesTests(unittest.TestCase):
    def fixture(self,p):
        inp,cfg=DailyTests().science(p,100,'VolumetricWaterContent')
        with inp.open() as f:
            reader=csv.DictReader(f);fields=reader.fieldnames;rows=list(reader)
        # Equal and conflicting duplicates straddle small transport batches. Both
        # remain inside the complete fixed-offset day passed to normalize_native.
        rows.insert(7,dict(rows[6]))
        conflict=dict(rows[151],v='-8.191');rows.insert(152,conflict)
        rows[20].update(v='',value_status='null')
        rows[21].update(v='',value_status='missing')
        rows[22].update(v='',value_status='invalid')
        rows=[r for i,r in enumerate(rows) if not (r['t'][:10]=='2024-03-04' and i%2)]
        m.bulk.write_csv(inp,rows,fields)
        cfg.update(version='dendra-bulk-daily-input-3',historical_bulk_policy=m.BULK_POLICY,
            historical_source_route=m.BULK_ROUTE,quality_admission_basis=m.BULK_BASIS,
            csv_sha256=m.bulk.sha(inp),native_rows=len(rows))
        for d in cfg['quality_days'].values():
            d.update(state=m.BULK_BASIS,reason='API_Q_UNAVAILABLE_BULK_READYTOUSE',query_complete=False)
        return cfg

    def run_science(self,p,cfg,name):
        m.bulk.write_json(p/(name+'.input.json'),cfg)
        m.bulk.run(['Rscript','--vanilla',ROOT/'scripts/dendra/bulk_daily.R',p/(name+'.input.json'),p/(name+'.json')])
        return json.loads((p/(name+'.json')).read_text())

    def bounded(self,cfg,batch=7,**kwargs):
        return dict(cfg,adapter_bounds=dict(whole_rows=100,batch_rows=batch,day_rows=1000,interval_bins=16,**kwargs))

    def test_two_pass_exact_science_across_transport_boundaries(self):
        with tempfile.TemporaryDirectory() as tmp:
            p=Path(tmp);cfg=self.fixture(p);whole=self.run_science(p,cfg,'whole')
            # Pinned against the pre-capacity-change adapter on this compact
            # fixture: the ordinary path retains its exact serialized science.
            self.assertEqual(hashlib.sha256((p/'whole.json').read_bytes()).hexdigest(),
                'f8d9cdd4a22c5d97affc15487ff8ebf7f205c1921bb1c1d36ce06c4ef317d4fb')
            for batch in (7,31,145):
                with self.subTest(batch=batch):
                    part=self.run_science(p,self.bounded(cfg,batch),'part'+str(batch))
                    accounting=part.pop('resource_accounting')
                    self.assertEqual(part,whole)
                    self.assertEqual(accounting['first_pass'],accounting['second_pass'])
                    self.assertEqual(accounting['first_pass']['raw_rows'],cfg['native_rows'])
                    self.assertEqual(accounting['first_pass']['normalized_rows'],cfg['native_rows']-2)
                    self.assertLessEqual(accounting['first_pass']['largest_batch_rows'],batch)
                    self.assertLessEqual(accounting['first_pass']['largest_day_raw_rows'],1000)
            rows=whole['rows']
            self.assertEqual(rows[0]['daily_mean_vwc_percent'],0)
            self.assertEqual(rows[0]['n_duplicate_rows'],1)
            self.assertEqual(rows[0]['n_null'],1);self.assertEqual(rows[0]['n_missing'],1)
            self.assertEqual(rows[0]['n_invalid'],1)
            self.assertEqual(rows[1]['n_duplicate_conflicts'],1)
            self.assertEqual(rows[1]['n_out_of_range'],1)
            self.assertEqual(rows[1]['daily_status'],'WITHHELD_BY_EXISTING_SCREEN')
            self.assertTrue(all(r['daily_mean_vwc_percent'] is None for r in rows[1:]))
            self.assertEqual(rows[-1]['daily_status'],'MISSING')
            self.assertEqual(rows[-1]['observation_count'],0)
            self.assertFalse(rows[-1]['source_empty']);self.assertFalse(rows[-1]['query_complete'])
            self.assertEqual(whole['context']['seconds'],600)

    def test_held_days_and_previous_cadence_identical(self):
        with tempfile.TemporaryDirectory() as tmp:
            p=Path(tmp);cfg=self.fixture(p)
            cfg['quality_days']['2024-02-28']['state']='QUARANTINED'
            cfg['quality_days']['2024-03-02']['state']='UNRESOLVED'
            whole=self.run_science(p,cfg,'whole');part=self.run_science(p,self.bounded(cfg),'part')
            part.pop('resource_accounting');self.assertEqual(part,whole)
            self.assertEqual(whole['rows'][0]['source_quality_status'],'QUARANTINED')
            self.assertEqual(whole['rows'][3]['daily_status'],'UNRESOLVED_SEMANTICS')

    def test_capacity_failures_leave_no_science_output(self):
        with tempfile.TemporaryDirectory() as tmp:
            p=Path(tmp);cfg=self.fixture(p)
            cases=[dict(self.bounded(cfg),adapter_bounds=dict(whole_rows=100,batch_rows=7,day_rows=100,interval_bins=16)),
                   dict(cfg,adapter_bounds=dict(m.ADAPTER_BOUNDS,batch_rows=65537)),
                   dict(self.bounded(cfg),native_rows=cfg['native_rows']+1),
                   dict(cfg,native_rows=m.MAX_NATIVE_ROWS+1),
                   dict(self.bounded(cfg),version='dendra-bulk-daily-input-2')]
            for i,case in enumerate(cases):
                with self.subTest(case=i),self.assertRaises(m.subprocess.CalledProcessError):
                    self.run_science(p,case,'bad'+str(i))
                self.assertFalse((p/('bad'+str(i)+'.json')).exists())

    def test_unordered_large_source_fails_without_sorting_or_loss(self):
        with tempfile.TemporaryDirectory() as tmp:
            p=Path(tmp);cfg=self.fixture(p)
            with Path(cfg['csv']).open() as f:
                reader=csv.DictReader(f);fields=reader.fieldnames;rows=list(reader)
            rows[6],rows[8]=rows[8],rows[6]
            m.bulk.write_csv(cfg['csv'],rows,fields);cfg['csv_sha256']=m.bulk.sha(cfg['csv'])
            with self.assertRaises(m.subprocess.CalledProcessError):self.run_science(p,self.bounded(cfg),'bad')
            self.assertFalse((p/'bad.json').exists())

    def test_cadence_tie_and_fractional_intervals_match_core(self):
        with tempfile.TemporaryDirectory() as tmp:
            p=Path(tmp);cfg=self.fixture(p)
            fields=['datastream_id','t','v','value_status','duplicate_conflict','alternative_out_of_range']
            for label,intervals in [('tie',[600,1200]*4),('fractional',[600.125]*8)]:
                rows=[];at=datetime(2024,2,28,8)
                for delta in [0]+intervals:
                    at+=timedelta(seconds=delta)
                    rows.append(dict(zip(fields,[cfg['stream_id'],at.strftime('%Y-%m-%dT%H:%M:%S.%fZ'),0,'number',False,False])))
                m.bulk.write_csv(cfg['csv'],rows,fields)
                cfg.update(csv_sha256=m.bulk.sha(cfg['csv']),native_rows=len(rows))
                whole=self.run_science(p,cfg,label+'whole')
                bounded=dict(cfg,adapter_bounds=dict(whole_rows=1,batch_rows=3,day_rows=20,interval_bins=3))
                part=self.run_science(p,bounded,label+'part');part.pop('resource_accounting')
                self.assertEqual(whole,part)
                self.assertEqual(whole['context']['seconds'],None if label=='tie' else 600.125)
                if label=='tie':
                    bounded['adapter_bounds']['interval_bins']=1
                    with self.assertRaises(m.subprocess.CalledProcessError):self.run_science(p,bounded,'binsbad')


if __name__=='__main__':unittest.main()
