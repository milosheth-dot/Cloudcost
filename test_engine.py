import sys
import tempfile
import unittest
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from engine import carbon, simulate, anomalies, demo_rows, validate, parse_csv, summarize
from app import Store

class EngineTests(unittest.TestCase):
    def setUp(self):
        self.row = dict(date='2026-01-01', provider='AWS', service='EC2', region='us-east-1',
                        resource_id='test', cost_usd=10, energy_kwh=10, intensity_g_per_kwh=400, pue=1.2)
    def test_units(self):
        self.assertAlmostEqual(carbon(self.row), 4.8)
    def test_identity(self):
        result=simulate([self.row], {})
        self.assertEqual(result['current'], result['projected'])
    def test_partial_shift(self):
        r=simulate([self.row], dict(share=50, cost_factor=.8, energy_factor=.5, target_intensity=100))
        self.assertAlmostEqual(r['projected']['cost'], 9)
        self.assertAlmostEqual(r['projected']['carbon'], 2.7)
    def test_zero_energy(self):
        r=simulate([self.row], dict(share=100, energy_factor=0))
        self.assertEqual(r['projected']['carbon'], 0)
    def test_selection(self):
        r=simulate([self.row], dict(resource_id='different', cost_factor=0))
        self.assertEqual(r['projected']['cost'],10)
        self.assertEqual(r['affected_rows'],0)
    def test_validation(self):
        for bad in [-1, float('nan'), float('inf'), 'oops']:
            with self.assertRaises(ValueError):
                validate([dict(self.row, energy_kwh=bad)])
        with self.assertRaises(ValueError):validate([self.row,self.row])
        with self.assertRaises(ValueError):parse_csv('date,cost_usd\n2026-01-01,10')
        with self.assertRaises(ValueError):simulate([self.row],dict(share=101))
    def test_demo_spike(self):
        rows=demo_rows();flags=anomalies(rows)
        self.assertEqual(len(flags),2)
        self.assertEqual({f['metric'] for f in flags},{'cost','carbon'})
        self.assertTrue(all(f['resources'][0]['resource_id']=='analytics' for f in flags))
    def test_sparse_days_not_baseline(self):
        from datetime import date,timedelta
        rows=[dict(self.row,date=(date(2026,1,1)+timedelta(days=30*i)).isoformat(),cost_usd=100 if i==9 else 10) for i in range(10)]
        self.assertEqual(anomalies(rows),[])
    def test_sqlite_persistence(self):
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'test.db'
            store=Store(path);dataset=store.add('Test',validate([self.row]))
            self.assertEqual(Store(path).rows(dataset),[self.row])
            self.assertEqual(len(store.rows('demo')),150)
            with self.assertRaises(ValueError):store.rows('unknown')
    def test_empty(self):
        self.assertEqual(summarize([])['cost'],0)
        self.assertEqual(anomalies([]),[])

if __name__=='__main__':unittest.main()
