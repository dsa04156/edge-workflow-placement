import numpy as np
from scenarios import Scenario
from run_suite import env
# Split topology is a negative control: queue correction must disappear.
a=Scenario('original','default',env('split',split=True),999);ra=a.run()
b=Scenario('queue','default',env('split',split=True),999);rb=b.run()
assert ra['decisions']==rb['decisions'] and ra['mean_ms']==rb['mean_ms']
assert all(r['records'][2]['node']==4 for r in b.rows)
assert all(r['records'][1]['node']<4 for r in b.rows)
c=Scenario('both','default',env('split',split=True),999)
p=c.costs(c.belief.means());c.cpu[4].free=100000
assert np.array_equal(p,c.costs(c.belief.means()))
# Controller responds only to its own observations, with VQA isolated from feature queue.
s=np.zeros((5,2));s[4,0]=10
assert np.all(c.costs(c.belief.means(),s)>9)
print('Split queue mapping, causal predictor, negative-control invariance: PASS')
