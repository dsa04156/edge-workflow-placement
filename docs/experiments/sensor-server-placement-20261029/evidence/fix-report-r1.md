# 리뷰 r1 수정 보고 — 센서 작업 전체의 서버 선택, 2026-10-29

Task `task_ee690705302b` / dispatch `ctx_6f86e3e85574`. 리뷰 r1 지적에 대한 최소 수정이며 재설계가 아니다.
**live 실행, 클러스터 호출, Git 작업, 외부 다운로드는 하지 않았다.**
수정한 파일은 `staged/experiments/gpu-mobilint-placement/`의 소스·테스트 4개와 `release/` 출력 3개, 그리고 이 보고서다.

## 1. 모델·인증 증거

| 항목 | 값 | 근거 |
|---|---|---|
| authMethod | `claude.ai` | `claude auth status` |
| apiProvider | `firstParty` | `claude auth status` |
| subscriptionType | `max` | `claude auth status` |
| model | `claude-opus-5-5` (Claude Opus 5.5) | 세션 시스템 정보 |
| override 환경변수 | `ANTHROPIC_*`, `CLAUDE_CODE_USE_*`, `CLAUDE_MODEL` 키 없음 | 키 이름만 확인 |

모델이나 공급자를 대체하지 않았다. TDD skill은 `/home/jinuk/.agents/skills/tdd/SKILL.md`에서 읽었다.
seam은 spec에 이미 정해져 있다: 공개 `table_from` / `profile_table` → `decide`.

## 2. 지적별 수정

### P2-1 probe payload 문구 (coordinator 수정 spec: size-matched synthetic probe 허용)

- **메커니즘은 바꾸지 않았다.**
  - `chain_generator.py`의 `os.urandom(size)` probe를 그대로 두었다.
  - main job은 계속 frozen 센서 입력(`cal-3733440`)을 보낸다.
- `chain_policy.py` 모듈 docstring:
  - "EXACT raw NPY payload"를 "size-matched SYNTHETIC payload (random bytes, the raw NPY's byte count; not the sensor content)"로 고쳤다.
  - "Main jobs always send the frozen sensor input." 문장을 추가했다.
- `chain_policy.probe_evidence`의 note를 "actual size-matched synthetic probe bytes"로 고쳤다.
- `chain_analyze.py` LIMITATIONS의 "probe RTT of the actual payload"를 다음으로 고쳤다:
  - size-matched synthetic payload (random bytes, raw NPY byte count; not the sensor content)
  - "Main jobs used the frozen sensor input." 문장 추가
- 수정 뒤 `grep -n "actual payload\|EXACT" chain_*.py test_*.py`의 결과는 0건이다.

### P2-2 원시 비용 수집이 검증을 우회하던 문제

- `chain_policy.is_measurement(value)`를 새로 두었다.
  - 받는 값: `int`/`float`이면서 `bool`이 아니고, finite이며 0보다 큰 값.
  - 그 밖의 값은 모두 거부: bool, 숫자 문자열, bytes, None, NaN/inf, 0 이하.
  - `_cost`는 같은 함수를 쓰도록 바꿨다. finite positive guard는 그대로이고 동작도 같다.
- `chain_policy.table_from`: 각 probe의 원시 `median_ms`를 `float()` 변환 **전에** 검사한다. 통과하지 못하면 `PolicyError`를 낸다.
- `chain_live.profile_table`: 원시 `receipt.worker_wall_ms`를 `float()` 변환 **전에** 검사한다.
  - 통과하지 못하면 problem(`... worker_wall_ms=... is not a finite positive measurement`)을 추가하고, 그 행을 비용에 넣지 않는다.
  - 이 실험은 reps1이라 해당 (stage, host)에 측정 행이 남지 않는다. 그래서 `no measured profile row`도 함께 나오고 `({}, {}, problems)`가 반환된다.
  - `Live.profile()`은 problems가 있으면 이미 `RuntimeError`로 중단한다(fail closed).
- 같은 역할을 하는 다른 경로도 확인했다.
  - generator의 `probes()`는 `statistics.median(good)`을 그대로 넘긴다. 이 값은 `table_from`에서 변환 전에 검사된다.
  - 그 밖에 비용을 `float()`로 수집하는 경로는 이 디렉터리에 없다(`grep table_from|profile_table|float(`로 확인).
- **정상 입력은 그대로 동작한다.**
  - 테스트에서 int/float probe median을 넣으면 기존 SLOW 선택과 같고, predicted 값도 GGG 970 / NNN 4124다.
  - 기존 profile fixture(`worker_wall_ms` 10.0/100.0)도 problem 없이 통과한다.

## 3. 테스트 (TDD red → green)

새 테스트는 `test_whole_server.Choices`에 2개 추가했다. 둘 다 공개 수집 함수에서 `decide`까지 거쳐 확인한다.

1. `test_raw_probe_medians_are_validated_before_conversion_ingestion_to_decision`
   - S→N probe의 `median_ms`에 다음 값을 넣는다: `True`, `False`, `'1.0'`, `'3400'`, `None`, NaN, inf, `0`, `-1.0`, `b'1'`.
   - 각 경우 `CP.decide(CP.table_from(...))`가 `PolicyError`를 내야 한다.
   - 정상 int/float 입력이면 기존과 같은 선택이 나와야 한다.
2. `test_raw_profile_timings_are_validated_before_conversion_ingestion_to_decision`
   - 비워밍업 `vlm@N` receipt의 `worker_wall_ms`에 다음 값을 넣는다: `True`, `'1.0'`, `'10'`, `None`, NaN, inf, `0`, `-1.0`.
   - 각 경우 다음을 확인한다:
     - `L.profile_table`이 `worker_wall_ms` problem을 반환한다.
     - `('vlm','N')`가 proc에 없다.
     - 이어지는 `CP.decide(CP.table_from(proc, probes))`가 `PolicyError`를 낸다.
   - 정상 fixture는 problem 없이 결정까지 간다.

Red 결과 (수정 전, 같은 명령):
```
FAIL: test_raw_probe_medians_...   AssertionError: PolicyError not raised : True
FAIL: test_raw_profile_timings_... AssertionError: False is not true : (True, [])
```
첫 실행에서 probe 테스트는 `choices` dict 전체를 비교하는 바람에 `selection_ms`(타이밍) 차이로 실패했다. 이는 테스트 자체의 결함이라 placement와 predicted만 비교하도록 고쳤다. 다시 실행하니 위의 red가 의도한 원인(`True`가 통과함)으로 실패했다.

Green 결과 (대상 테스트만; 이전 65개 전체는 다시 돌리지 않음):
```bash
cd .scratch/sensor-server-placement-20261029/staged/experiments/gpu-mobilint-placement
PY=../../../../gpu-mobilint-vlm-20261028/env/mblt-x86/bin/python
PYTHONPATH=.:../quality-network-baselines:../quality-network-heterogeneous:../real-topology-live \
  rtk proxy $PY -m unittest -v test_whole_server test_chain_live.Qualification test_chain.Policies
# -> Ran 25 tests in 0.067s, OK
#    = test_whole_server 20개(새 2개 포함) + test_chain_live.Qualification(profile_table) 1개 + test_chain.Policies 4개(table_from 사용)
```
- 다른 테스트 파일은 `table_from`이나 `profile_table`을 부르지 않는다(grep으로 확인).
- loopback/r1 테스트는 calibration 경로를 거치지 않는다.
- `__pycache__`는 삭제했다.

## 4. release 갱신 (서명 없음)

```bash
cd .scratch/sensor-server-placement-20261029
L=staged/experiments/gpu-mobilint-placement/chain_live.py
rtk proxy $PY $L --plan                > release/plan.json            # rc 0, 이전과 바이트 동일
rtk proxy $PY $L --print-release-draft > release/release-draft.json   # rc 0, approved_by=null
# 임시 사본(scratchpad)에 approved_by='TEMPORARY-DRY-RUN-TEST-MARKER-not-a-live-authorization'를 넣고:
rtk proxy $PY $L --dry-run --release <임시 사본>   # rc 0 {"release":"ok","design_sha256":"0c87f0ad…fbb5","code_files":36}; 사본은 바로 삭제
rtk proxy $PY $L --dry-run --release release/release-draft.json   # rc 1 "release check failed (no kubectl was run): release needs approved_by"
```
- 이전 draft와 비교하면 `code` 외의 키(design, design_sha256, frozen, inputs, prepared 등)는 모두 같다.
- code hash는 계속 36개이고, 바뀐 것은 2개뿐이다:
  - `chain_live.py` → `4c62ba8be3fd1c04f9d8cea9110ddb81e54131058b44775072e4f3099782ebb5`
  - `chain_policy.py` → `8847924ec09ce885c92e8687427d204b2769119099c1bc7a18433190798fd383`
- 동적으로 로드되는 `G.__file__`(`experiments/gpu-mobilint-vlm/live.py`)은 계속 포함된다.
- `chain_analyze.py`는 이전과 같이 release에 묶이지 않는다(developer-report §6.8).
- **임시 marker는 live 승인이 아니다.** 최종 release 승인은 coordinator의 몫이다.

## 5. 최종 hash (sha256)

| 파일 | sha256 |
|---|---|
| `release/release-draft.json` | `436f630f3eda62baf09dc385d635dd610aa01c6bc733f0fd83c9a9d110d38a23` (이전 `9aa275cc…1eeb`) |
| `release/plan.json` | `e4afd44b1cdeb9f2f8145b0f386d4ec6beefaceb2ffeabf87abc34c6e717168e` (변화 없음) |
| `release/dry-run.out.json` | `486f4a0f47f89795a0be6b2bbdb80320d40e5c3811f656a37be90dd55f317248` (내용 변화 없음) |
| `chain_policy.py` | `8847924ec09ce885c92e8687427d204b2769119099c1bc7a18433190798fd383` |
| `chain_live.py` | `4c62ba8be3fd1c04f9d8cea9110ddb81e54131058b44775072e4f3099782ebb5` |
| `chain_analyze.py` | `bf6ba76f55642a8a80bec911a8bfb11631235e70cf8682da969d2a444d9a4515` |
| `test_whole_server.py` | `7f78a4976553ba28c539817ee9748547fbacadcb6718eaad0ccf8070ff18535e` |

- `review-r1-baseline/`의 나머지 9개 파일(`chain_design`, `chain_generator`, `chain_stages`, `chain_worker`, 테스트 5개)은 staged 파일과 바이트 동일하다(`cmp`).
- 원본 실험 소스, `frozen/`, AGENTS/docs/artifacts, 형제 staged 디렉터리는 건드리지 않았다.

## 6. 남은 사항

- live 실행, 실제 route·장치 동작 검증, release 승인은 coordinator의 몫이다.
- probe는 여전히 합성 바이트다. 네트워크 비용은 크기가 같은 비압축 전송 비용이며, 센서 내용과는 무관하다(수정된 spec에 따른 것).
- main job은 frozen 센서 입력을 사용한다.

## 7. diff (review-r1-baseline → 수정본)

```diff
--- a/chain_policy.py	2026-10-29 22:15:58.688066411 +0900
+++ b/chain_policy.py	2026-10-29 22:31:18.810531359 +0900
@@ -4,9 +4,10 @@
 never DAG placement; the three stage costs are summed only to price one colocated server.
 Cost table (measured in the calibration phase, frozen before any evaluation request):
   proc[(stage, host)]      closed-loop stage processing time (worker receipt worker_wall_ms, no queue; warm-up excluded)
-  link[(S, host, 0)]       source -> server: median application-path probe RTT of the EXACT raw NPY payload from the source's
-                           network position over the condition's route (the owned gateway) to a sink on the server; HTTP/relay
-                           overhead included: an application path cost, never a pure network time.
+  link[(S, host, 0)]       source -> server: median application-path probe RTT of a size-matched SYNTHETIC payload (random
+                           bytes, the raw NPY's byte count; not the sensor content) from the source's network position over the
+                           condition's route (the owned gateway) to a sink on the server; HTTP/relay overhead included: an
+                           application path cost, never a pure network time. Main jobs always send the frozen sensor input.
   link[(host, host, 1|2)]  same-host local stage HTTP (PNG, VLM JSON) on that server; part of the server's processing cost.
   There is no inter-server link: a probe/key between G and N is refused.
   processing(h) = T1(h) + local(h,1) + T2(h) + local(h,2) + T3(h);   network(h) = link(S, h, 0).
@@ -31,12 +32,18 @@
     pass


+def is_measurement(value):
+    """A raw measured ms is a finite positive int/float; bool, numeric strings and anything else are refused (check BEFORE
+    any float() conversion, which would turn True or '1.0' into a valid-looking 1.0)."""
+    return not isinstance(value, bool) and isinstance(value, (int, float)) and math.isfinite(value) and value > 0
+
+
 def _cost(table, kind, key):
     try:
         value = table[kind][key]
     except (KeyError, TypeError):
         raise PolicyError(f'cost table lacks {kind}{key!r}') from None
-    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value <= 0:
+    if not is_measurement(value):
         raise PolicyError(f'cost {kind}{key!r}={value!r} is not a finite positive measurement')
     return value

@@ -140,10 +147,13 @@


 def table_from(proc, probes):
-    """proc {(stage, host): ms}; probes [dict(src, dst, edge, median_ms)] -> cost table (tuple keys). Inter-server probes refused."""
+    """proc {(stage, host): ms}; probes [dict(src, dst, edge, median_ms)] -> cost table (tuple keys). Inter-server probes and
+    a raw median that is not a finite positive measurement are refused before conversion."""
     for p in probes:
         if p['src'] != D.ORIGIN and p['src'] != p['dst']:
             raise PolicyError(f"inter-server probe {p['src']}->{p['dst']} refused: the whole job runs on one server")
+        if not is_measurement(p.get('median_ms')):
+            raise PolicyError(f"probe {p['src']}->{p['dst']}#{p['edge']} median_ms={p.get('median_ms')!r} is not a finite positive measurement")
     return dict(proc=dict(proc), link={(p['src'], p['dst'], p['edge']): float(p['median_ms']) for p in probes})


@@ -155,7 +165,7 @@
         ms = p.get('median_ms')
         out.append(dict(src=p['src'], dst=p['dst'], edge=p['edge'], kind=p.get('kind'), bytes=p['bytes'], route=p.get('route'), median_ms=ms,
                         effective_app_mbps=p['bytes'] * 8 / (ms / 1000) / 1e6 if ms else None,
-                        note='derived from actual payload bytes / probe RTT incl. HTTP/relay processing; not a link capacity'))
+                        note='derived from actual size-matched synthetic probe bytes / probe RTT incl. HTTP/relay processing; not a link capacity'))
     return out


--- a/chain_live.py	2026-10-29 22:20:10.088490881 +0900
+++ b/chain_live.py	2026-10-29 22:31:18.810731833 +0900
@@ -732,7 +732,8 @@
 def profile_table(rows, ids, run, npy_bytes):
     """Profile rows -> (proc {(stage, host): median measured worker_wall_ms}, sizes, problems). Every row must be ok and its
     receipt the verified worker's (UID/node/device/run); all four inputs must have passed the in-Pod T1 roundtrip on both
-    hosts; T2 must have answered the frozen input id; every (stage, host) needs measured rows; payload sizes must agree."""
+    hosts; T2 must have answered the frozen input id; every (stage, host) needs measured rows (a raw worker_wall_ms that is
+    not a finite positive int/float is a problem, never converted); payload sizes must agree."""
     problems, proc, outs = [], {}, {}
     for r in rows:
         rec = r.get('receipt') or {}
@@ -751,6 +752,9 @@
         if r['stage'] == 'vlm' and (timing.get('input_id') != D.INPUT or rec.get('model_sha256') != ids[r['host']]['model_sha256']):
             problems.append(f"{r['request_id']}: T2 input/model {timing.get('input_id')}")
         if r.get('purpose') == 'profile' and not r['warmup']:
+            if not chain_policy.is_measurement(rec.get('worker_wall_ms')):  # raw value, before any conversion: fail closed
+                problems.append(f"{r['request_id']}: worker_wall_ms={rec.get('worker_wall_ms')!r} is not a finite positive measurement"[:300])
+                continue
             proc.setdefault((r['stage'], r['host']), []).append(float(rec['worker_wall_ms']))
             outs.setdefault((r['stage'], r['host']), set()).add(r['output_bytes'])
     roundtrip = {(r['input_id'], r['host']) for r in rows if r.get('purpose') == 'roundtrip' and r.get('status') == 'ok'
--- a/chain_analyze.py	2026-10-29 22:17:38.827308162 +0900
+++ b/chain_analyze.py	2026-10-29 22:31:18.810802243 +0900
@@ -44,8 +44,9 @@
     'npu_slow2 is the unchanged net_control.py 2 Mbit/s tbf on the owned gateway egress toward the NPU Pod IP only (cloud=[N], '
     'edge=[]); G and source replies unshaped; both servers reached through the same gateway in both conditions. Not a '
     'measured physical WAN/topology; no shared-bottleneck claim.',
-    'Network cost is the application-path probe RTT of the actual payload over the request route (HTTP/relay processing '
-    'included), not a pure network time; derived effective throughput is not a link capacity or the configured rate.',
+    'Network cost is the application-path probe RTT of a size-matched synthetic payload (random bytes, the raw NPY byte '
+    'count; not the sensor content) over the request route (HTTP/relay processing included), not a pure network time; '
+    'derived effective throughput is not a link capacity or the configured rate. Main jobs used the frozen sensor input.',
     'Same-host stage HTTP is part of the server processing estimate; T1/T3 are host-CPU support stages, only T2 runs '
     'neural inference on CUDA or Mobilint (no CPU fallback).',
     'Policy labels selecting the same server share ONE observation (aliases), never independent samples. The chooser was '
--- a/test_whole_server.py	2026-10-29 22:16:04.569891104 +0900
+++ b/test_whole_server.py	2026-10-29 22:30:59.144066687 +0900
@@ -129,6 +129,33 @@
                 with self.assertRaises(CP.PolicyError, msg=(name, policy)):
                     CP.select(policy, table)

+    def test_raw_probe_medians_are_validated_before_conversion_ingestion_to_decision(self):
+        probes = [dict(src=a, dst=b, edge=e, median_ms=v) for (a, b, e), v in SLOW['link'].items()]
+        got = CP.decide(CP.table_from(SLOW['proc'], probes))  # standard int/float medians still work
+        self.assertEqual({p: c['placement'] for p, c in got['choices'].items()},
+                         dict(fixed_gpu='GGG', fixed_npu='NNN', compute_only='NNN', compute_network='GGG'))
+        self.assertEqual(got['predicted_ms'], {'GGG': 970, 'NNN': 4124})
+        for bad in (True, False, '1.0', '3400', None, math.nan, math.inf, 0, -1.0, b'1'):
+            raw = [dict(p, median_ms=bad) if (p['src'], p['dst'], p['edge']) == ('S', 'N', 0) else p for p in probes]
+            with self.assertRaises(CP.PolicyError, msg=repr(bad)):
+                CP.decide(CP.table_from(SLOW['proc'], raw))
+
+    def test_raw_profile_timings_are_validated_before_conversion_ingestion_to_decision(self):
+        from test_chain_live import Qualification as Rows
+        probes = [dict(src=a, dst=b, edge=e, median_ms=v) for (a, b, e), v in SLOW['link'].items()]
+        ids, rows = Rows().rows()
+        proc, _, problems = L.profile_table(rows, ids, 'gmp-q', NPY)
+        self.assertEqual(problems, [])
+        CP.decide(CP.table_from(proc, probes))  # standard finite positive numeric timings still work
+        for bad in (True, '1.0', '10', None, math.nan, math.inf, 0, -1.0):
+            raw = json.loads(json.dumps(rows))
+            next(r for r in raw if (r['stage'], r['host'], r['warmup']) == ('vlm', 'N', False))['receipt']['worker_wall_ms'] = bad
+            proc, _, problems = L.profile_table(raw, ids, 'gmp-q', NPY)
+            self.assertTrue(any('worker_wall_ms' in p for p in problems), (bad, problems))
+            self.assertNotIn(('vlm', 'N'), proc, repr(bad))
+            with self.assertRaises(CP.PolicyError, msg=repr(bad)):
+                CP.decide(CP.table_from(proc, probes))
+
     def test_alias_sharing_and_four_main_jobs_maximum(self):
         choices = {'relay': CP.aliases(CP.decide(RELAY)), 'npu_slow2': CP.aliases(CP.decide(SLOW))}
         self.assertEqual(choices['relay'], {'GGG': ['fixed_gpu'], 'NNN': ['compute_network', 'compute_only', 'fixed_npu']})
```
