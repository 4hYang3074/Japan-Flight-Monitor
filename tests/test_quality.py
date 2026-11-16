import copy
import json
import sys
import tempfile
import unittest
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scraper'))
import scan
import build
import watch
import quality
import yaml


def html(entries=None, block=3, payload=None):
    if payload is None:
        payload = [None, None, None, [None]]
        payload[block] = [entries]
    return '<script class="ds:1">AF_initDataCallback({data:' + json.dumps(payload) + ', sideChannel: {}});</script>'


def item(code='D7', price=900):
    leg = [None] * 22
    leg[3], leg[6], leg[8], leg[10] = 'KUL', 'KIX', [2, 20], [10]
    leg[20] = leg[21] = [2027, 4, 5]
    return [[code, ['AirAsia X'], [leg]], [[], 'token'] if price is None else [[None, price], 'token']]


class ParserTests(unittest.TestCase):
    def test_priced_and_unpriced(self):
        self.assertEqual([f['price'] for f in scan.parse(html([item(), item('MH', price=None)]))], [900, None])

    def test_both_result_blocks(self):
        self.assertEqual(len(scan.parse(html(payload=[None, None, [[item()]], [[item('MH')]]]))), 2)
        self.assertEqual(scan.parse(html([item()], block=2))[0]['price'], 900)

    def test_duplicate_blocks_keep_quote(self):
        flights = scan.parse(html(payload=[None, None, [[item(price=None)]], [[item()]]]))
        self.assertEqual(len(flights), 1)
        self.assertEqual(flights[0]['price'], 900)

    def test_invalid_price_is_parse_failure(self):
        for price in (-1, True, '900'):
            with self.assertRaises(ValueError):
                scan.parse(html([item(price=price)]))

    def test_explicit_empty(self):
        for empty in (None, []):
            self.assertEqual(scan.parse(html(empty)), [])

    def test_missing_or_malformed_not_no_flights(self):
        for body in ('<html></html>', html(payload=[None]*4), html(payload={}), html(payload=[None,None,None,{}]), html([[[]]])):
            with self.subTest(body=body), self.assertRaises((ValueError, IndexError, TypeError)):
                scan.parse(body)

    def test_source_errors(self):
        for body in ('<script class="ds:1">{data:[],errorHasStatus: true, x:0}</script>', '<title>Before you continue</title>'):
            with self.assertRaises(scan.SourceError):
                scan.parse(body)

    @patch('scan.time.sleep')
    def test_retries_and_error_classification(self, sleep):
        body = '<script class="ds:1">{data:[],errorHasStatus:true, x:0}</script>'
        with patch('scan.fetch_flights_html', return_value=body) as fetch:
            flights, error = scan.run(None)
            self.assertEqual(fetch.call_count, scan.RETRIES)
            self.assertEqual(flights, [])
            self.assertTrue(error.startswith('SourceError:'))
        with patch('scan.fetch_flights_html', side_effect=[body, html([item()])]):
            self.assertIsNone(scan.run(None)[1])
        with patch('scan.fetch_flights_html', return_value='<html></html>'):
            self.assertEqual(quality.query_status(0, 0, scan.run(None)[1]), 'parse_error')
        with patch('scan.fetch_flights_html', side_effect=TimeoutError):
            self.assertEqual(quality.query_status(0, 0, scan.run(None)[1]), 'source_error')


class PipelineTests(unittest.TestCase):
    def setUp(self):
        self.cfg = yaml.safe_load((build.ROOT/'config.yaml').read_text())
        self.cfg['routes'] = self.cfg['routes'][:1]
        self.route = self.cfg['routes'][0]
        self.route['direct_airlines'] = {'D7': 'AirAsia X', 'MH': 'Malaysia Airlines'}
        self.now = datetime.now(timezone.utc).replace(microsecond=0)

    def record(self, code='D7', price=900, inbound=False):
        d = datetime(2027, 4, 12 if inbound else 5, 2, 20)
        return {'airline': self.route['direct_airlines'][code], 'code':code, 'price':price,
                'from':'KIX' if inbound else 'KUL', 'to':'KUL' if inbound else 'KIX',
                'dep':d.isoformat(sep=' ', timespec='minutes'),
                'arr':(d+timedelta(hours=7)).isoformat(sep=' ', timespec='minutes'),
                'stops':0, 'via':[], 'minutes':420, 'fetched_at':self.now.isoformat()}

    def raw(self, price=None, absent=False):
        rr = {direction:[self.record('MH', 1000, inbound)] + ([] if absent else [self.record(price=price,inbound=inbound)])
              for direction,inbound in [('outbound',False),('inbound',True)]}
        cov = [{'route':'KUL-KIX','code':code,'airline':name,'count':int(not absent or code=='MH'),
                'priced':int(code=='MH' or price is not None),'error':False,
                'status':'ok' if code=='MH' or price is not None else 'no_flights' if absent else 'unpriced'}
               for code,name in self.route['direct_airlines'].items()]
        return {'routes':{'KUL-KIX':rr},'coverage':cov,'errors':[], 'only':[],
                'window':['2027-03-20','2027-04-20'], 'scanned_at':self.now.isoformat()}

    def test_query_states(self):
        self.assertEqual([quality.query_status(*x) for x in [(0,0),(1,0),(1,1),(0,0,'SourceError: X'),(0,0,'ValueError: X')]],
                         ['no_flights','unpriced','ok','source_error','parse_error'])

    @patch('scan.time.sleep')
    def test_scan_day_preserves_unpriced_and_status(self, sleep):
        cfg=self.cfg; cov=[]; errors=[]
        f=scan.parse(html([item(price=None)]))
        with patch('scan.fetch', return_value=(f,None)):
            records=scan.scan_day('KUL','KIX',date(2027,4,5),self.route,cfg,errors,cov,{'D7'})
        self.assertEqual(len(records),1)
        self.assertEqual(cov[0]['status'],'unpriced')
        self.assertEqual(errors,[])

    def test_old_price_ttl_and_no_synthetic_flights(self):
        rr=self.raw()['routes']['KUL-KIX']; cache={}
        for r in rr['outbound']+rr['inbound']:
            cache[build.leg_key(r)]=[900,(self.now-timedelta(days=6)).isoformat()]
        build.fill_old_prices(rr,{'D7'},cache,self.now)
        rows=build.combine(rr,self.cfg,self.cfg['sakura']['full_bloom'])
        self.assertTrue(next(r for r in rows if r['ak']=='AirAsia X')['old'])
        rr=self.raw()['routes']['KUL-KIX']
        build.fill_old_prices(rr,{'D7'},cache,self.now+timedelta(days=2))
        self.assertIsNone(rr['outbound'][1]['price'])
        rr=self.raw(absent=True)['routes']['KUL-KIX']
        build.fill_old_prices(rr,{'D7'},cache,self.now)
        self.assertEqual([r['code'] for r in rr['outbound']],['MH'])

    def test_missing_airline_and_no_combination(self):
        raw=self.raw(absent=True)
        q=quality.summarize(self.route,raw['coverage'],[])
        self.assertEqual(q['D7']['status'],'no_flights')
        self.assertEqual(q['MH']['status'],'no_combinations')
        q=quality.summarize(self.route,[],[])
        self.assertEqual(q['D7']['status'],'not_scanned')
        del raw['coverage'][0]['status']
        self.assertEqual(quality.summarize(self.route,raw['coverage'],[])['D7']['status'],'unknown_empty')

    def test_roundtrip_quote_counts_when_oneway_unpriced(self):
        raw=self.raw();rr=raw['routes']['KUL-KIX']
        rr['roundtrip']=[{'code':'D7','out_dep':rr['outbound'][1]['dep'],'ret_dep':rr['inbound'][1]['dep'],'price':1500,'fetched_at':self.now.isoformat()}]
        rows=build.combine(rr,self.cfg,self.cfg['sakura']['full_bloom'])
        self.assertEqual(quality.summarize(self.route,raw['coverage'],rows)['D7']['status'],'ok')

    def test_build_watch_and_gate_integration(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp); docs=root/'docs';docs.mkdir();(root/'data').mkdir()
            (root/'config.yaml').write_text(yaml.safe_dump(self.cfg))
            raw=self.raw();(root/'data/raw.json').write_text(json.dumps(raw))
            cache={build.leg_key(self.record()):[900,(self.now-timedelta(days=1)).isoformat()],
                   build.leg_key(self.record(inbound=True)):[900,(self.now-timedelta(days=1)).isoformat()]}
            (docs/'leg_prices.json').write_text(json.dumps(cache))
            with patch.multiple(build,ROOT=root,DOCS=docs),patch.multiple(watch,ROOT=root,DOCS=docs):
                build.main()
                d=json.loads((docs/'data.json').read_text());hist=json.loads((docs/'history.json').read_text())
                self.assertTrue(d['routes'][0]['incomplete'])
                self.assertEqual(d['routes'][0]['cheapest']['p'],2000)
                self.assertTrue(next(a for a in d['routes'][0]['airlines'] if a['name']=='AirAsia X')['old'])
                self.assertFalse(any(h['airline']=='AirAsia X' for h in hist))
                self.assertTrue(quality.check(root/'data/raw.json'))
                raw['only']=['D7'];raw['routes']['KUL-KIX']={k:[r for r in v if r['code']=='D7'] for k,v in raw['routes']['KUL-KIX'].items()}
                (root/'data/raw_watch.json').write_text(json.dumps(raw));watch.main()
                w=json.loads((docs/'watch.json').read_text())[-1]['routes'][0]
                self.assertEqual(w['combos'],0);self.assertEqual(w['quality']['D7']['status'],'unpriced')
                raw=self.raw(price=800);(root/'data/raw.json').write_text(json.dumps(raw));build.main()
                self.assertFalse(quality.check(root/'data/raw.json'))
                d=json.loads((docs/'data.json').read_text())
                self.assertFalse(d['routes'][0]['incomplete'])
                self.assertEqual(d['routes'][0]['cheapest']['p'],1600)
                raw=self.raw(absent=True);(root/'data/raw.json').write_text(json.dumps(raw));build.main()
                d=json.loads((docs/'data.json').read_text())
                self.assertEqual(d['routes'][0]['quality']['D7']['status'],'no_flights')
                self.assertEqual(d['routes'][0]['cheapest']['p'],2000)


if __name__=='__main__':
    unittest.main()
