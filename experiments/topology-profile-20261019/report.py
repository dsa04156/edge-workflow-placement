from pathlib import Path
import json,zipfile,collections,gzip
import numpy as np
ROOT=Path(__file__).resolve().parent;repo=ROOT.parent.parent
D=json.loads((ROOT/'results.json').read_text() if (ROOT/'results.json').exists() else gzip.decompress((ROOT/'results.json.gz').read_bytes()));summary=D['summary'];runs=D['runs'];verification=json.loads((ROOT/'verification.json').read_text())
lines=['# DAG 토폴로지·성능 프로파일 기반 배치 실험','', '## 제안 방식','', '워크플로 DAG의 분기·합류, 고정된 센서 위치, 인프라 네트워크의 실제 모델 경로와 링크 비용, 작업×노드 P75 실행 성능을 함께 사용해 예측 완료 시간을 최소화하는 배치. CPU·메모리 자원 제약은 모든 방식에 동일하게 적용했다. 여기서 실제 모델 경로는 시뮬레이터에 정의된 경로이며 실측 네트워크를 뜻하지 않는다.','', '세 연산자(영상 특징 추출, 음성 인식, VQA)의 작은 DAG이므로 가능한 배치 최대27개를 전수 탐색했다. 전수 탐색 자체를 신규 알고리즘으로 주장하지 않는다.','', '## 비교 설계','', '|방식|DAG|네트워크 토폴로지·센서 위치|노드별 작업 성능|','|---|---|---|---|','|자원 점수 근사|배치에 미사용|미사용|미사용|','|토폴로지만|사용|사용|동일 작업은 모든 노드에서 같은 시간으로 가정|','|성능만|사용|전송 비용0으로 가정|사용|','|제안 방식|사용|사용|사용|','', '성분 제거 비교군도 제안 방식과 같은 탐색 엔진과 자원 제약을 사용한다. 평가 시에는 모든 방식에 실제 시뮬레이션 토폴로지와 동일한 성능 표본을 적용한다. 자원 점수 방식은 Kubernetes v1.35.0 두 자원 플러그인의 Python 근사이며 kube-scheduler 실측이 아니다.','', '144조건 × 12시드 × 4방식 = 6,912개 정책 결과. 두 작업 조합, 두 센서 배치, 세 네트워크 구조, 세 WAN 대역폭, 두 요청률, VQA 이동 가능/클라우드 고정. 자세한 사전 설정은 DESIGN.md.','', '## 동일 부하에서 연결 구조 변화','', '경량 작업, 센서 E0/E1 분리, WAN10Mbps, 초당0.1건, VQA클라우드 고정. 평균 완료 시간(ms).','', '|토폴로지|자원 점수 근사|토폴로지만|성능만|제안 방식|','|---|---|---|---|---|']
for topo in ['edge_fast','edge1_slow','cloud_hub']:
 m={r['comparator']:r for r in summary if (r['workload'],r['sensors'],r['topology'],r['bw'],r['rate'],r['sink'])==('light','split',topo,10,.1,'cloud')}
 lines.append('|'+ '|'.join([topo]+[f"{m[p]['baseline_mean_ms']:.2f}" for p in ['resource_proxy','topology_only','profile_only']]+[f"{m['resource_proxy']['full_mean_ms']:.2f}"])+'|')
lines+=['','## 전체 조건: 효과의 방향','','아래 개수는 점 추정치의 부호이며 통계적 유의성 개수가 아니다. CI는 시드 단위 부트스트랩5,000회, 다중비교 보정 없음.','', '|제안 방식의 비교 대상|개선|동일|악화|시드별 상대 개선율 범위%|','|---|---|---|---|---|']
for comp in ['resource_proxy','topology_only','profile_only']:
 a=[r['paired_improvement_pct'] for r in summary if r['comparator']==comp]
 lines.append(f"|{comp}|{sum(x>1e-8 for x in a)}|{sum(abs(x)<=1e-8 for x in a)}|{sum(x< -1e-8 for x in a)}|{min(a):.2f} ~ {max(a):.2f}|")
lines+=['','## 위 예시의 배치와 전송량','','노드0=E0(CPU1 프로파일),1=E1(CPU5 프로파일),2=Cloud(GPU1 프로파일). 순서=(영상,음성,VQA). 빈도는12시드 중 선택 횟수.','', '|구조|방식|배치 빈도|WAN KiB/요청|critical-path 연산/전송/대기 ms|','|---|---|---|---|---|']
for topo in ['edge_fast','edge1_slow','cloud_hub']:
 for pol in ['resource_proxy','topology_only','profile_only','topology_profile']:
  a=[r for r in runs if (r['workload'],r['sensors'],r['topology'],r['bw'],r['rate'],r['sink'],r['policy'])==('light','split',topo,10,.1,'cloud',pol)]
  count=collections.Counter(tuple(r['plan']) for r in a);label='; '.join(f'{p} ×{n}' for p,n in count.items())
  wan=np.mean([r['wan_bytes'] for r in a])/1024;parts=[np.mean([r[k] for r in a]) for k in ['critical_compute_ms','critical_network_ms','critical_queue_ms']]
  lines.append(f'|{topo}|{pol}|{label}|{wan:.3f}|'+ '/'.join(f'{v:.2f}' for v in parts)+'|')
ms=[r['planning_ms']['topology_profile'] for r in D['planning']]
lines+=['','## 검증 및 해석 범위','', f"- 실행 {verification['wall_seconds']:.2f}초. 전체 배치 계산 시간 중앙값 {np.median(ms):.3f}ms, 최댓값 {max(ms):.3f}ms (현재 컨테이너의 소규모 문제 기준; E2E에 포함하지 않음).",'- 경로 검증: 엣지 간 직접 경로가 없는 cloud_hub에서 두 WAN 링크를 통과한다. 고속 엣지 구조와 다른 경로/지연이 실제 평가에 반영된다.','- 병렬 분기·합류, 동일 노드 직렬 처리, 자원 제약, 동일 배치 결과 재사용, E2E critical-path 분해 일치 검증을 통과했다.','- 합류 DAG의 두 분기 시간을 단순 합산하지 않는다. 최종 완료를 결정한 경로의 연산·전송·큐 대기만 E2E 분해에 포함한다.','- results.json에 모든 시드의 배치/trace hash, 제약, 예측값, 실제 모델 평가값을 저장했고 summary.csv에 두 가지 개선율(시드별 상대비율 평균과 평균끼리의 비율)을 구분했다.','- rho>=1이면 해당 고정부하의 정상상태 성능으로 해석하면 안 된다. link_rho/node_rho를 기록했다.','- 성능 프로파일과 데이터 크기는 공개 JellyBean 자료지만 토폴로지·네트워크 값·자원 용량은 합성 가정이다. 실제 센서/노드/네트워크 실측은 아니다.','- GPU자원 독점 요청, 실제 다중코어 병렬 실행, 배경 작업 간섭, 콜드스타트, 모델 전송, TCP/손실, 대규모 DAG는 포함하지 않았다. 노드당 하나의 처리 lane이라는 강한 단순화가 있다.','- P75 단일요청 예측을 최적화하므로 변동성/큐가 있는 평가 평균을 반드시 최소화하지 않는다. 성분 제거 비교군보다 나쁜 경우도 숨기지 않는다.','- 실제 Kubernetes 대비 개선, 기존 연구 대비 신규성 및 일반 DAG 확장성은 아직 입증하지 않았다.','- Semantica 연결 도구가 없어 해당 기록은 수행하지 못했다.','', '## 재현 및 출처','', 'NumPy가 있는 Python에서 `python experiments/topology-profile-20261019/run.py` 후 `python experiments/topology-profile-20261019/report.py`. 공개 CSV와 의존 코드/소스 라이선스를 함께 제공한다.','', '- JellyBean 공식 프로파일: https://github.com/libertyeagle/JellyBean ; https://arxiv.org/abs/2205.04713','- Kubernetes 자원 점수식: https://github.com/kubernetes/kubernetes/tree/v1.35.0/pkg/scheduler/framework/plugins/noderesources','- K-Dense experimental-design 스킬의 고정 요인설계, paired blocking, seed-level replication 원칙 적용.']
lines += ['','## 핵심 관찰','', '고속 엣지 연결에서 제안 방식은 영상 특징 추출을 E1, 음성 인식과 VQA를 Cloud에 배치했다. 느린 E1 연결에서는 영상을 E0, 음성을 E1에서 처리하고 VQA만 Cloud에 배치했다. 노드 성능이 같아도 연결 구조가 바뀌면 선택이 달라진다.', '고속 엣지 예시에서 토폴로지만 고려하면 전송량은 더 작지만 느린 노드의 연산 비용으로179.88ms가 걸렸고, 두 정보를 함께 고려하면141.08ms였다. 전송량 최소화가 곧 완료시간 최소화는 아니다.', '이 관찰은 DAG·인프라 토폴로지·작업별 노드 성능을 결합하는 문제 설정을 지지하는 시뮬레이션 근거다. 모든 조건에서 추가 이득이 보장되거나 실제 Kubernetes 성능 개선이 확인된 것으로 해석하면 안 된다.']
(ROOT/'REPORT.md').write_text('\n'.join(lines));print('\n'.join(lines[21:45]))
with zipfile.ZipFile(repo.parent/'edge_workflow_topology_profile.zip','w',zipfile.ZIP_DEFLATED) as z:
 for f in ROOT.rglob('*'):
  if f.is_file() and '__pycache__' not in str(f):z.write(f,str(f.relative_to(repo)))
 for rel in ['experiments/dag-placement-20261019/run.py','experiments/dag-placement-20261019/DESIGN.md','experiments/profiled-placement-20261019/run_cloud_required.py','experiments/profiled-placement-20261019/README.txt','experiments/simulation-20261019/legacy.py']:
  f=repo/rel;z.write(f,rel)
 for folder in ['experiments/dag-placement-20261019/sources','experiments/simulation-20261019/data']:
  for f in (repo/folder).rglob('*'):
   if f.is_file():z.write(f,str(f.relative_to(repo)))
