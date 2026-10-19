from pathlib import Path
import json,hashlib
import numpy as np
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
ROOT=Path(__file__).resolve().parent
s=json.loads((ROOT/'results/summary.json').read_text());rows=s['results']
labels={'reference':'기준 혼잡 조건','light_cloud':'후속 추론 ×1','medium_cloud':'후속 추론 ×12','steady':'일정한 배경 부하','burst':'요청 폭주','steady_burst':'일정 부하+요청 폭주','slow_edges':'엣지 처리시간 ×4','fast_edges':'엣지 처리시간 ×0.25','slow_cloud_feature':'클라우드 특징 처리시간 ×4','large_raw':'원본 데이터 ×8','small_raw':'원본 데이터 ×0.125','large_features':'특징 데이터 ×8','small_features':'특징 데이터 ×0.125','slow_wan':'WAN 25 Mbps','high_rtt':'WAN RTT 160 ms','slow_wan_high_rtt':'25 Mbps+RTT 160 ms','resnet18':'ResNet18','resnet101':'ResNet101','resnet152':'ResNet152','edge_drift':'E2 노드 중간 성능 저하','low_rate':'초당 0.5 요청','overload':'초당 4 요청·과부하','split_cloud':'클라우드 별도 처리 대기열','split_cloud_burst':'별도 대기열+요청 폭주'}
lines=['엣지–클라우드 워크플로: 24개 진단 시나리오 추가 실험',
'','결론: 공유 클라우드 FCFS 대기열을 재방문하는 비용의 예측 보정이 주된 효과다. 범용적인 신규 스케줄링 알고리즘이나 능동 프로파일링의 우월성은 입증하지 않았다.',
'실행: 24개 사전 지정 시나리오 × 12개 새로운 seed(601–612) × 4개 방식 = 1,152회. 시뮬레이션이며 실제 장비 실행은 아니다.',
'4개 방식: original(기존 Thompson 배치 예측), links(자체 전송 일정 반영), queue(클라우드 재진입 대기 보정), both(두 보정). 모든 방식의 서비스 학습·사전분포·관측 권한 동일. 외부 선행연구 알고리즘과 직접 비교한 결과가 아니다.',
'',f"이번 24조건 동일 가중 평균 개선: {s['aggregate_pct'][0]:.2f}% (seed-block bootstrap95% CI {s['aggregate_pct'][1]:.2f}–{s['aggregate_pct'][2]:.2f}%).",
'중요: 이번 조건은 이전에 효과가 확인된 혼잡 조건을 중심으로 한 진단 설계다. 이전 전체환경 평균 2.84%보다 알고리즘이 개선됐다는 뜻이 아니다. 알고리즘은 고정했고 환경 구성이 바뀌었다. 실제 환경 빈도에 따른 일반 성능으로 해석할 수 없다.',
f"5% 이상 개선 + 개별95% CI 하한>0 + 24개 비교 Holm p<.05: {s['passes']}/24조건.",
'','측정과 설계',
'센서 입력은 E0에서 준비된 것으로 시작. E0 전처리 → E0/E1/E2/C 중 한 곳의 특징 추출 → 클라우드 후속 추론(VQA) → E0 응답. 전체 경로 E2E, p95, 1초 SLO 초과율, 90초 이내 완료율, 종료 후 drain을 기록.',
'기준: ResNet50, 초당2요청, WAN300Mbps/RTT40ms, 90초, 30초 단위 교대 배경 부하, 후속 추론 시간 ×50. ×50은 공개 서비스 시간의 합성 배율이며 실제 무거운 모델 실행값이 아니다.',
'대부분 기준에서 한 요인만 변경. 원본·특징 크기를 독립 변경; 대역폭·RTT도 독립 변경. burst는 10초마다 요청률0.25/0.25/2.5배로 반복하므로 기대 총요청 수 유지. 모델별 정확도가 같다는 가정은 하지 않으며 각 조건 안에서는 모델 고정.',
'별도 대기열 조건: 기존 클라우드 특징 추출 서버에 더해, 같은 클라우드 위치의 별도 VQA 서버와 독립 배경 부하를 추가. 모든 방식이 이 구조를 알며 공정하게 별도 대기열을 예측. 자원 수를 맞춘 공유/분리 구조 우열 실험은 아니다. queue 보정이 사라지도록 구현된 음성 대조이므로 0% 결과만으로 실제 시스템의 인과 기전을 증명할 수 없다.',
'동일 조건·seed에서 모든 방식의 도착/배경 해시와 요청 수 일치를 검증. 요청 개수를 독립 반복으로 세지 않고 seed12개를 분석 단위로 사용. 표본 수는 연산 예산 기준이며 검정력 보장은 없음.',
'개선율은 seed별 (기존−both)/기존의 평균. 표의 지연 평균으로 직접 계산한 비율과 약간 다를 수 있음. 10,000회 paired bootstrap CI는 개별 구간; Holm은24개 양측 paired t 검정에 적용. 분포 가정 및 작은 표본에 대한 제약은 남음.',
'','전체 결과 (음수는 악화; 유의성 없는 작은 차이도 모두 포함)',
'조건 | 기존 ms | both ms | 개선 % [95% CI] | Holm p | 사전 기준 통과']
for r in rows:
 lines.append(f"{labels[r['name']]} | {r['original_ms']:.2f} | {r['both_ms']:.2f} | {r['improvement_pct']:.2f} [{r['ci_low']:.2f}, {r['ci_high']:.2f}] | {r['holm_p']:.4g} | {r['passes']}")
lines+=['','해석',
'1. 일정 배경 부하, 느린WAN, 빠른엣지, 별도클라우드대기열에서는 개선이 작거나 없다. 기저 방식이 이미 유리한 배치를 고르면 추가 보정의 여지가 작다는 해석과 일치한다.',
'2. ResNet18/101/152 등 특징 추출 모델 변경과 느린 엣지, 큰 RTT, 요청 폭주 조건에서도 공유 대기열 보정의 이득은 남는다. 다만 대부분 같은 혼잡·무거운 후속 처리 가정 주변이다.',
'3. 링크 보정 단독의 이득은 대체로 작다. 외부 WAN 경쟁 트래픽을 넣지 않은 실험이므로 네트워크 비용 자체가 중요하지 않다는 결론은 불가능하다.',
'4. 초당4요청 조건은 필수 VQA+장기 평균 배경 부하 하한이1을 초과한다. 지연이 줄어도 안정화하지 못한다. 90초 이후 입력/배경을 멈춰 끝까지 처리한 유한 구간의 개선이다.',
'5. 가장 큰 40%대 결과는 탐색한 조건 중 최대값이며 일반 성능 주장으로 쓰지 않는다. 이번 단계의 기여는 효과가 유지되는 조건과 사라지는 조건을 구분한 것이다.',
'','부가 지표',
'조건 | p95 기존→both ms | SLO1초 초과 기존→both % | 90초내 완료 기존→both %']
for r in rows:
 lines.append(f"{labels[r['name']]} | {r['original_p95_ms']:.1f}→{r['both_p95_ms']:.1f} | {r['original_slo_pct']:.2f}→{r['both_slo_pct']:.2f} | {100*r['original_completed_fraction']:.2f}→{100*r['both_completed_fraction']:.2f}")
lines+=['','한계와 다음 검증',
'현재 전체 경로는 고정 직렬 3단계다. 분기/합류 DAG, 여러 센서 소스, 정확도-지연 모델 선택, 외부 링크 혼잡, 컨테이너 기동/전환비용, 메모리·GPU 동시성, 실제 하드웨어 실행은 검증하지 않았다. 스케줄러 계산 시간은 실행 중 기록되지만 시뮬레이션 E2E에 포함되지 않는다.',
'다음은 기존 예측기의 대기 누락을 고친 강한 기준선과 비교하고, 실제 엣지+클라우드에서 처리 대기열 공유/분리 여부를 먼저 측정해야 한다. 과거의 능동 프로파일링 연구 질문과 이번 예측 보정 효과를 구분해야 한다.',
'','재현',
'Python3.12, numpy/scipy/matplotlib. python3 verify_suite.py; python3 run_suite.py; python3 analyze_suite.py; python3 make_report.py. 작업 폴더는 이 패키지 디렉터리. 설계는 results/design.json, 개별 실행은 results/runs.jsonl, 요약은 results/summary.csv/json.',
'설계 파일은 실험 전에 기록. 분석 스크립트 작성/표시는 실행 후 완료. 사전 분석 규칙은 설계 파일에 기록. 코드 변경 이력의 사전 해시와 최종 해시는 별도 보존.',
'','출처와 방법론',
'공개 서비스 프로파일: https://github.com/libertyeagle/JellyBean commit65a1d67817946a4ae67b068eafb49d649bc061bc (포함 LICENSE 참조). 서비스 프로파일 외 부하/네트워크/배율은 합성.',
'experimental-design 스킬을 실험 단위, 짝지음, 분석 사전 지정에 사용. Kassis, T., Agarwal, V., He, Y., Patel, D., & Brueckner, A. M. (2026). Scientific Agent Skills: A Library of Procedural Knowledge for Research Agents. https://doi.org/10.48550/arXiv.2609.00065 (2026-10-19KST 최신 레코드 확인).']
(ROOT/'REPORT.txt').write_text('\n'.join(lines))
fig,ax=plt.subplots(figsize=(10,9))
y=np.arange(len(rows));m=np.array([r['improvement_pct'] for r in rows]);lo=np.array([r['ci_low'] for r in rows]);hi=np.array([r['ci_high'] for r in rows])
ax.barh(y,m,color=['#247b75' if r['passes'] else '#aaa' for r in rows])
ax.errorbar(m,y,xerr=[np.maximum(0,m-lo),np.maximum(0,hi-m)],fmt='none',ecolor='#222',capsize=2)
ax.set_yticks(y,[r['name'] for r in rows]);ax.invert_yaxis();ax.axvline(0,color='black',lw=.6)
ax.set_xlabel('Mean paired E2E latency reduction (%) vs original');ax.set_title('24 diagnostic scenarios / 12 seeds / 1,152 simulation runs\nGreen: >=5% reduction, pointwise CI >0 and Holm p<0.05')
ax.grid(axis='x',alpha=.2);fig.tight_layout();fig.savefig(ROOT/'scenarios.png',dpi=160)
(ROOT/'manifest.json').write_text(json.dumps({str(p.relative_to(ROOT)):hashlib.sha256(p.read_bytes()).hexdigest() for p in ROOT.rglob('*') if p.is_file() and '__pycache__' not in str(p) and p.name!='manifest.json'},indent=2))
