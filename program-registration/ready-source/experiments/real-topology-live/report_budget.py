from run_smoke import NODES
"""Descriptive budget-pilot report, only after independent evidence audit."""
import argparse
import json
from pathlib import Path


def report(run,out):
    read=lambda name:json.loads((run/name).read_text())
    audit=read('placement-verification-v1.json')
    if audit['status']!='passed':raise ValueError('Passing audit required')
    summary=read('pilot-summary.json');design=read('design.json')
    origin=read('calibration-origin.json') if (run/'calibration-origin.json').exists() else None
    lines=['# 서버 배치 예산에 따른 greedy·DAG 공동 결정 비교','',f"Run `{run.name}`, collector `{read('git-source.json')['commit']}`. 감사 **passed**.",'',
        '서버 작업 슬롯을2개로 제한한 워크플로 전용 admission-budget 실험이다. 물리 서버 자원 부족·실제 경쟁 부하를 재현한 것이 아니다. 다른 작업을 막는 filler나 호스트 설정 변경 없이 선택된 배치의 예산 준수를 실행 전에 검사했다.', '',
        '이미지 E0·음성 E1 고정, 동일3Task·CPU 모델·입력·품질·자원 요청을 사용했다. 20개 보정 입력의 비용표로 계획을 동결했고, 평가 split7–12의6입력을0.1rps로 실행했다. 같은 host/cache와 동결 프로파일을 공유한 두 시간 블록이므로 독립 환경 반복이나 통계적 우월성으로 해석하지 않는다.', '',
        '## 관측 평균 E2E','', '| 블록·조건 | greedy | HEFT 변형 | 정확 열거 | greedy 한 번 교환 |', '|---|---:|---:|---:|---:|']
    if origin:
        from datetime import datetime
        age=(datetime.fromisoformat(origin['reused_at'])-datetime.fromisoformat(origin['calibration_finished_at'])).total_seconds()/60
        lines[7:7]=[f"보정 원본은 `{origin['run']}`이며 이 실행은 평가만 재개했다. 마지막 보정 이벤트에서 재개까지 {age:.2f}분 경과했다. 실행 코드7개·bundle·원본 hash를 확인했고 재개 시작 시점의 물리 자원 snapshot으로 계획을 다시 검증했다. 각 Pod의 실제 admission은 Kubernetes가 수행한다. 이전 시도는 평가0건이며, 재개 실행의 새 보정으로 주장하지 않는다.",'']
    policy_names=('greedy','heft','ours','greedy-swap')
    effects=[]
    for condition,policies in sorted(summary['mapping'].items()):
        vals={p:summary['observations'][policies[p]] for p in policy_names}
        formatted=[f"{vals[p]['mean_ms']:.2f}ms ({vals[p]['success']}/{vals[p]['attempts']})" if vals[p]['mean_ms'] is not None else f"실패 ({vals[p]['success']}/{vals[p]['attempts']})" for p in policy_names]
        lines.append('| '+condition+' | '+' | '.join(formatted)+' |')
        g=vals['greedy']['mean_ms'];e=vals['ours']['mean_ms'];s=vals['greedy-swap']['mean_ms']
        if g is not None and e is not None:
            effects.append(dict(condition=condition,greedy_minus_exact_ms=g-e,exact_reduction_pct=(g-e)/g*100,swap_minus_exact_ms=None if s is None else s-e))
    lines+=['','성공 요청 평균이며 실패 건수도 분모에 유지했다. 동일 배치를 선택한 정책은 같은 관측을 공유한다.','',
        '## 블록별 차이','', '| 조건 | greedy−정확 열거 | 정확 열거의 감소율 | 단순 교환−정확 열거 |','|---|---:|---:|---:|']
    for row in effects:
        residual='NA' if row['swap_minus_exact_ms'] is None else f"{row['swap_minus_exact_ms']:.2f}ms"
        lines.append(f"| {row['condition']} | {row['greedy_minus_exact_ms']:.2f}ms | {row['exact_reduction_pct']:.2f}% | {residual} |")
    lines+=['','음수는 정확 열거 쪽이 더 느렸다는 뜻이다. 사전 비용표의 최적해가 관측 지연의 최적해임을 뜻하지 않는다.','',
        '## 고유 배치','', '| 관측 | 정책 | 영상 / 음성 / 결합 |','|---|---|---|']
    aliases={v:k.upper() for k,v in NODES.items()}
    for condition,policies in sorted(summary['mapping'].items()):
        for label in sorted(set(policies.values())):
            config=read(label+'.config.json');names=[p for p,v in policies.items() if v==label]
            placement='/'.join(aliases[config['placement'][t]] for t in ('vision','speech','fusion'))
            lines.append(f"| {label} | {', '.join(names)} | {placement} |")
    deployments=[read(label+'-deployment.json')['elapsed_ms'] for label in summary['observations']]
    lines+=['','## 검증과 해석 범위','',
        f"- 평가{audit['evaluation_successes']}/{audit['evaluation_attempts']}성공, 워밍업 포함{audit['successes_including_warmup']}성공, receipt{audit['receipts']}건. 최대 score 오차{audit['max_score_abs_error']:.9g}.",
        '- 입력/출력 hash·Top5·Pod UID/노드·실제 CPU/thread·예산·정책 재계산·단순 교환 재계산·drain·차단/복구·소유 리소스 정리를 감사했다.',
        f"- 새 배포 준비 시간은{min(deployments)/1000:.2f}–{max(deployments)/1000:.2f}초로 별도 보존했다. 위 요청 지연에는 배포·전체 보정 비용이 들어 있지 않다. 실제 온라인 전환의 서비스 중단 비용이나 net benefit은 측정하지 않았다.",
        '- greedy는 입력 topological order의 earliest finish, HEFT는 상주 서비스 의미로 옮긴 rank/EFT 변형, 정확 열거는 작은 DAG의 reference selector다. 새 알고리즘이 아니다.',
        '- greedy-swap은 greedy 결과의 작업 쌍 위치를 한 번 바꾼 feasible 후보들과 원래 배치 중 같은 비용표로 최저를 선택한다. repair decision_ms는 원래 greedy 선택시간을 제외하므로 합계 계산 시 둘을 더해야 한다.',
        '- 전달 비용은 실제 payload echo RPC 대리 지표이며 공유 링크 경합·큐를 예측하지 않는다. 모델 정확도 평가와 CPU reference 일치를 구분한다.',
        '- 사전12개 조건 탐색과 새 실측을 구분한다. 서버1슬롯에서 순서 변경으로 해소되는 경우와2Mbps의 정책 일치 조건도 제외하지 않았다.',
        '- 관측 지연·프로파일 획득 비용·변화 지속시간·재배포 비용을 포함한 동적 비교는 남아 있다. 정적 개선을 새 알고리즘 필요성이나 동적 순이익으로 확대하지 않는다.', '',
        '## 원자료','',f'원자료는 로컬 `{run}`에 보존한다. Git에는 요약과 파일 hash manifest만 포함한다. 다른 checkout에서 재감사하려면 원자료와 모델 bundle을 별도로 전달해야 한다.', '',
        '```bash',f'python3 experiments/real-topology-live/verify_placement.py {run}',f'python3 experiments/real-topology-live/report_budget.py {run} --out {out}','```','',
        '출력은 덮어쓰지 않는다. 외부 Pod 상태 변화는 원자료 protected-after.json을 확인하며 인과를 가정하지 않는다.','']
    with out.open('x') as f:f.write('\n'.join(lines))
    return effects

if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('run',type=Path);parser.add_argument('--out',type=Path,required=True)
    a=parser.parse_args();print(json.dumps(report(a.run,a.out),indent=2))
