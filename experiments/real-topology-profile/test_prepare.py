import copy,unittest,json
from pathlib import Path
import prepare
from analyze import summarize
class Tests(unittest.TestCase):
    def setUp(self):self.c=json.loads(Path(__file__).with_name('fixture.json').read_text())
    def test_measured_gate(self):
        with self.assertRaises(ValueError):prepare.validate(self.c)
        prepare.validate(self.c,True)
    def test_join_is_max(self):
        for t in self.c['tasks']:
            t['compute_ms']={n:1 for n in t['candidates']}
            for e in t['inputs']:e['transfer_ms']={k:0 for k in e['transfer_ms']}
        for e in self.c['outputs']:e['transfer_ms']={k:0 for k in e['transfer_ms']}
        p={'vision':'edge-a','speech':'edge-b','fusion':'cloud-c'}
        self.assertEqual(prepare.cost(self.c,p,'topology_profile'),2)
    def test_cycle_rejected(self):
        self.c['tasks'][0]['inputs']=[{'task':'fusion','bytes':1,'transfer_ms':{}}]
        with self.assertRaises(ValueError):prepare.validate(self.c,True)
    def test_capacity(self):
        for v in self.c['nodes'].values():v['cpu_free_m']=0
        with self.assertRaises(ValueError):prepare.plan(self.c,'topology_profile')
    def test_baseline_and_planned_pinning(self):
        r=prepare.plan(self.c,'topology_profile')
        for policy in ['resource','topology_profile']:
            items=prepare.manifests(self.c,policy,'test',r)['items'][1:]
            for pod,t in zip(items,self.c['tasks']):
                self.assertNotIn('nodeName',pod['spec'])
                values=pod['spec']['affinity']['nodeAffinity']['requiredDuringSchedulingIgnoredDuringExecution']['nodeSelectorTerms'][0]['matchFields'][0]['values']
                self.assertEqual(values,t['candidates'] if policy=='resource' else [r['placement'][t['id']]])
    def test_ablation_same_constraints(self):
        for policy in prepare.POLICIES[1:]:self.assertTrue(prepare.feasible(self.c,prepare.plan(self.c,policy)['placement']))
    def test_failures_preserved(self):
        base={'run_id':'r','condition':'c','block':'b','policy':'p','warmup':False}
        a=[dict(base,request_id='1',status='ok',quality_ok=True,e2e_ms=2),dict(base,request_id='2',status='timeout',quality_ok=False),dict(base,request_id='3',status='ok',quality_ok=False,e2e_ms=1)]
        g=summarize(a)[0];self.assertEqual((g['attempts'],g['valid'],g['failures'],g['quality_failures']),(3,1,1,1))
        with self.assertRaises(ValueError):summarize(a+a)
if __name__=='__main__':unittest.main()
