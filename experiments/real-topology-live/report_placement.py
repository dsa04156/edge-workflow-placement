"""Describe a verified pilot without treating shared observations as replicates."""
import argparse
import json
from pathlib import Path

from run_smoke import NODES


def read(path):
    return json.loads(path.read_text())


def report(run, output):
    audit=read(run/'placement-verification-v1.json')
    summary=read(run/'pilot-summary.json')
    plans=read(run/'plans-frozen.json')
    baseline=read(run/'baseline-placement.json')
    aliases={v:k.upper() for k,v in NODES.items()}
    policies=['kube-default','server-only','greedy','heft','ours','no-network','no-node-profile']
    lines=['# 보유 장비 자동 배치 파일럿', '',f'Run: `{run.name}`. 검증 상태: **{audit["status"]}**.', '',
        '고정 엣지 이미지 E0·음성 E1에서 ResNet18 특징 추출, Wav2Vec2 ASR, MUTAN 합류를 실행했다. '
        '각 Task는 품질 검증을 통과한 Pi 두 대와 CPU 서버 중에서 선택한다. 모델 선택·GPU·실제 센서 스트림 실험이 아니다.', '',
        '단일 시간 block의 압축 입력 S1/S2 실행 파일럿이다. 같은 날짜/장비/host cache를 공유하며, '
        '배치마다 새 Pod를 만들었어도 독립 환경 반복으로 해석하지 않는다. 보정20입력과 평가40입력은 분리했고, '
        '이번 파일럿 평가는 고정된 첫6개 평가 입력을 사용했다. 정책별 같은 배치는 한 관측을 공유한다.', '',
        '## 정책별 관측', '', '값은 성공 요청의 평균 E2E ms다. 실패는 다음 표의 분모에 유지한다.', '',
        '| 조건 | '+' | '.join(policies)+' |', '|---|'+'---:|'*len(policies)]
    for condition,mapping in summary['mapping'].items():
        cells=[]
        for policy in policies:
            label=mapping.get(policy)
            value=summary['observations'][label]['mean_ms'] if label else None
            cells.append(f'{value:.2f}' if value is not None else 'infeasible')
        lines.append('| '+condition+' | '+' | '.join(cells)+' |')
    lines+=['','## 실제 고유 배치와 성공률','','배치 표기는 V/S/F 순서다. C는 보유 CPU 서버이며 외부 클라우드가 아니다.','','| 조건 | 정책 | V/S/F | 실행 관측 | 성공/계획 |','|---|---|---|---|---|']
    for condition,mapping in summary['mapping'].items():
        groups={}
        for policy,label in mapping.items():groups.setdefault(label,[]).append(policy)
        for label,names in groups.items():
            config=read(run/(label+'.config.json'));obs=summary['observations'][label]
            placement='/'.join(aliases[config['placement'][t]] for t in ('vision','speech','fusion'))
            lines.append(f'| {condition} | {", ".join(names)} | {placement} | `{label}` | {obs["success"]}/{obs["attempts"]} |')
    lines+=['','## 실행·검증', '',
        f'- 평가 {audit["evaluation_successes"]}/{audit["evaluation_attempts"]} 성공. 워밍업 포함 성공 {audit["successes_including_warmup"]}건, stage receipt {audit["receipts"]}건.',
        f'- 최종 출력의 CPU reference 대비 최대 score 절대 오차: {audit["max_score_abs_error"]:.9g}. Top5 순서·모델/입출력 hash·실제 Pod UID/노드·실제 serving thread1을 대조했다.',
        '- 각 Task×노드에서 워밍업5 후60입력 품질 확인; 비용 계산은 calibration20만 사용했다. 세 Task는 순차로 프로파일링하며 비선택 모델을 평가 중 상주시키지 않았다.',
        '- 실제 default-scheduler가 공통 후보 중 선택한 배치를 기록한 뒤 probe Pod를 제거했다. 그 선택과 다른 정책 선택을 같은 방식으로 새로 배포하고 고유 배치 실행 순서를 무작위화했다.',
        '- 각 조건 마지막 두 원시 커널 backlog 관측, 전체 서비스 idle, 직접 경로 차단/복구와 소유 리소스 정리를 감사했다.', '',
        '## 해석 경계', '',
        '- 계획기는 p75 고립 연산시간과 실제 payload echo RPC 비용의 DAG critical path를 사용한다. echo는 직렬화·HTTP 왕복을 포함한 전달 대리 지표이며 실제 /execute·/join 프로토콜 전체나 순수 단방향 네트워크 시간이 아니다. 같은 노드 보정도 서로 다른 Pod 사이로 전달했다.',
        '- /open은 작은 echo 하나, 최종 결과는 역방향 payload echo 하나로 근사한다. 공유 링크 동시 전송·연산 간섭·큐·관측/배포 비용은 예측 목적함수에 포함되지 않는다. 예측값을 실제 total_ms 최적해로 부르지 않는다.',
        '- HEFT는 upward rank와 earliest-finish 선택을 독립 상주 서비스 실행 의미에 맞춘 변형이다. 원 논문의 단일 처리기 lane 실행을 재현했다는 주장이 아니다. 정확 열거도 새 알고리즘이 아니다.',
        '- 공유 WAN은 실험 소유 gateway Pod의 방향별 상한과 지연이다. TCP가 중계에서 분리되며,100Mbps는 상한 설정이지 실효100Mbps 달성 주장이 아니다.',
        '- 이 파일럿은 통계적 우월성·장기 SLO·새 알고리즘 필요성의 증거가 아니다. 동일 배치 정책 간 수치 중복은 동일 관측을 공유한 결과다.',
        '- native 직접 경로 대조, raw 별도 보정/sweep, 독립 block 반복·전수 배치 확인, source 위치 S3, Jetson S4와 부하 S5는 후속 단계다.', '',
        '## 원자료와 재실행', '',f'원자료: `{run}`. 코드와 배포/프로파일/선택/실패/정리 자료를 보존했다.', '',
        '```bash','rtk proxy python3 -u experiments/real-topology-live/run_placement.py',
        f'rtk proxy python3 experiments/real-topology-live/verify_placement.py {run}',
        f'rtk proxy python3 experiments/real-topology-live/report_placement.py {run} --out {output}','```','',
        '첫 명령은 준비된 대용량 로컬 bundle이 필요하며 실험 리소스를 생성한다. 검증/보고서는 기존 출력이 있으면 덮어쓰지 않는다.', '',
        '보호 리소스 종료 관측:', '```json',json.dumps(audit['protected'],ensure_ascii=False,indent=2),'```','']
    with output.open('x') as handle:handle.write('\n'.join(lines))


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('run',type=Path);parser.add_argument('--out',type=Path,required=True)
    args=parser.parse_args();report(args.run,args.out)
