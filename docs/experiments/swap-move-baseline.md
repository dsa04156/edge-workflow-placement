# greedy-swap-move 오프라인 사전 등록 기준선

2026-10-20. **오프라인 비용표 계획 도구이며 실측·실환경 검증이 아니다.**
명세는 `.scratch/swap-move-baseline/spec.md`다.

## 목적

[서버 슬롯 비교 완주 시도](budget-completion-attempt.md)의 사후 진단은 "greedy 한 번
교환 뒤 최대 한 작업 이동"이 동결 비용표에서 정확 열거와 같은 배치를 찾는다는
반대 근거였다. 다만 사후 선택이었고 선택 시간·후보 수가 사전에 고정되지 않았다.
이 작업은 새 실측 전에 그 단순 보완을 코드·규칙·hash로 고정한다. 단순 보완이
충분하다는 성능 주장이나 새 알고리즘 필요성의 근거로 쓰지 않는다.

## Claude와의 검토 결정

사용자 요청에 따라 Claude와 개선 우선순위를 논의했다. Claude는 새 데이터를
수집하기 전에 단순 기준선을 고정하고, 실측 재개는 제어 연결 검증 뒤에 하도록
제안했다. 호스트는 기존 정책 파일의 바이트 검증을 깨지 않도록 별도 모듈을
선택했다. 전체 실행 가능 공간의 크기를 매번 추가 열거하자는 제안은 채택하지
않고, 실제 검사한 국소 후보 수를 기록했다. 기존 greedy 내부의 전체 열거 비용은
명시적으로 남긴다.

구현은 `codex-on-crack:crack`의 solo/terminal 경로로 위임했다. 어댑터 결과에서
`claude-opus-5-5`, subscription, `apiKeySource=none`, 오류·권한 거부·fallback 0을
확인했다. 원문 대화·인증 자료는 Git에 넣지 않는다. 이는 구현 경로 확인이며
실험 성능 검증을 뜻하지 않는다.

## 결정 규칙

`experiments/real-topology-live/swap_move_baseline.py`의 공개 선택기 `greedy_swap_move(profile)`:

1. greedy: 기존 `placement_policies.sequential` 그대로.
2. 교환: 기존 `budget_placement.simple_alternatives`의 greedy-one-pair-swap과 같은 규칙.
   greedy 키 순서의 모든 작업 쌍을 한 번 바꾼 후보와 greedy 중 최소.
3. 이동: 교환 결과에서 작업 하나만 다른 후보 노드로 바꾼 후보와 무이동 중 최소. 한 번만
   수행하며 수렴까지 반복하지 않는다.
4. 비교 키는 기존과 같은 `(topology_profile 대리 비용, sorted(placement.items()))`. 비용이
   같으면 사전순으로 작은 배치가 선택되므로, 무이동보다 사전순이 작은 동일 비용 이동이
   있으면 그 이동이 선택될 수 있다(기존 사후 진단과 같은 동작).
5. 모든 후보는 `validate_placement`(완전성·자격 후보·유효 admission budget)로 검증한다.
   단계별 생성 제안, 미자격 거부, 용량 거부, 검증 통과 고유 배치 수(기준 배치 포함)를 기록한다.

`plan(profile, server)`는 기존 `select_budget_policies`의 4정책(greedy, heft, ours,
greedy-swap)에 greedy-swap-move를 더한다. 새 선택기의 greedy·교환 배치가 기존 정책과
다르면 실패한다. 기존 `budget_placement.py`, `placement_policies.py`,
`analyze_single_move.py`, `run_budget_placement.py`, verify/report 경로는 바꾸지 않았다.

## 시간 경계

`timing_ms`는 greedy, swap, move, repair(swap+move), total(greedy 포함)을 따로 기록한다.
기존 greedy는 완성 가능성 확인을 위해 전체 실행 가능 조합을 열거하므로 total을
확장 가능한 greedy의 비용으로 부르지 않는다. repair 단계는 전체 조합을 열거하지 않고
교환 쌍 수와 작업별 후보 수만큼만 검사한다. 시간은 로컬 Python 단일 실행값이며
반복 측정·분포·온라인 전환 비용이 아니다. 기존 greedy-swap의 `decision_ms`는 speech-first
진단 계산을 포함하는 기존 정의 그대로 두었다.

## 출력 계약

출력은 `contract=swap-move-baseline-offline-v1`, `execution_ready=false`인 계획 JSON이다.
provenance에 server, profile 파일 sha256, 정규화 profile digest, profile 경로,
4개 구현 파일(`swap_move_baseline.py`, `budget_placement.py`, `placement_policies.py`,
`prepare.py`) sha256을 남긴다. 기존 수집기·validator는 4정책 집합 계약을 가지므로 이
5정책 계획으로 호출하지 않는다. 이 출력은 실측 실행에 바로 쓸 수 없다.

CLI는 측정 provenance(`prepare.validate`), 서버의 profile 노드 여부, `admission_budget.server`
일치를 검사한다. 실패하면 종료 코드2와 함께 출력 파일을 만들지 않는다. 출력 디렉터리가
없거나 출력 파일이 이미 있으면 거부하며 `open('x')`로 배타적으로 생성한다.

## 실행한 명령

```bash
# 새 테스트와 관련 기존 테스트
cd experiments/real-topology-live
rtk proxy python3 -m unittest test_placement_policies test_budget_placement test_swap_move_baseline
# real-topology-live 전체(32건)와 prepare(7건)
rtk proxy python3 -m unittest discover -p 'test_*.py'
cd ../real-topology-profile && rtk proxy python3 -m unittest test_prepare

# 보존 profile 호환성 확인(출력은 임시 디렉터리, 기존 artifact 미변경)
cd ../..
rtk proxy python3 experiments/real-topology-live/swap_move_baseline.py \
  artifacts/topology-profile-live/wan-20261020t082011/profile-b0-compressed-10.json \
  --server etri-ser0002-cgnmsb --out NEW_PLAN.json
```

결과: 13건(관련) / 32건(전체 live) / 7건(prepare) 모두 통과. 같은 출력 경로 재실행은
`Refusing to overwrite`로 종료 코드2였다.

호스트도 저장소 루트에서 다음 명령으로 32건·7건을 독립 재검증했다.

```bash
rtk proxy python3 -m unittest discover -s experiments/real-topology-live -p 'test_*.py'
rtk proxy python3 -m unittest discover -s experiments/real-topology-profile -p 'test_prepare.py'
```

기준 커밋 `54620d2`와 비교해 기존 정책·분석기·수집기·감사기·보고서 6파일의 바이트
불변을 확인했다. 기존 `analyze_single_move.py`를 임시 출력 경로로 실행해 보존 진단의
모든 condition 결과를 재현했다. 같은 두 비용표의 반복 entry이며 독립 관측이 아니다.

테스트는 손계산3노드 사례(greedy C/C/E1=131ms → 교환 E1/C/C=95ms → 영상 E1→E0 이동
55ms, 정확 최적과 같음), 미자격 교환 거부와 한 번 이동으로 남는 잔여(115ms 대 정확 55ms),
전 배치 동률의 결정적 선택과 무이동 유지, 개선 없음, 입력 비변경, 5정책 계획,
CLI provenance·배타적 생성·잘못된 profile/server의 무출력을 확인한다. 2·3번째 slice의
일부 테스트는 1번째 slice 구현으로 처음부터 통과했으며, red는 모듈·`plan` 부재 단계에서 확인했다.

## 보존 snapshot 호환성

`wan-20261020t082011`의 현재 `prepare.py`/`budget_placement.py`/`placement_policies.py`
hash는 동결 `planner-source.py`·`collector-source/`와 같다(각각 `5fd6d9b4…`, `bd06dd6f…`,
`5d3f10a0…`). 새 모듈 hash는 `208d389c…`(이 문서 작성 시점)다.

| 동결 profile | greedy 대리 | 교환 대리 | 이동 | swap-move 대리 | 이동 단계 제안/용량 거부/고유 검증 |
|---|---:|---:|---|---:|---|
| b0-compressed-10 (`d09e1088…`) | 1,160.77ms | 969.39ms | 영상 E1→E0 | 954.08ms | 6/3/4 |
| b0-compressed-2 (`fbed2ecd…`) | 953.35ms | 953.35ms | 없음 | 953.35ms | 6/1/6 |

교환 단계는 두 profile 모두 제안3, 고유 검증3이었다. 4개 기존 정책 배치는
`plans-frozen.json`과 같고, greedy-swap-move 배치·대리 비용·고유 후보 수는
[사후 진단](../../artifacts/budget-completion/fresh-single-move.json)의 selected·
selected_proxy_ms·feasible_candidates와 같다(테스트 `FrozenRunCompatibilityTests`, 로컬
artifact가 없으면 skip). 이는 같은 동결 비용표의 재계산이며 새 관측이 아니다. 실측
지연(872.51ms 등)과 대리 비용을 섞거나, b1 entry(같은 비용표 복제)를 독립 확인으로 세지 않는다.

## 한계

- 실환경 검증 없음. 클러스터·네트워크 변경·새 수집을 하지 않았다.
- 대리 비용은 큐·동시 링크 경쟁을 무시한 critical path이며 실측 종단 지연이 아니다.
- 한 번 이동은 국소 탐색이다. 두 작업 이상 이동이 필요한 경우 잔여가 남는다(테스트 사례).
- 3작업·3노드 규모에서 확인했다. 큰 DAG에서의 품질·시간은 확인하지 않았다.
- 선택 시간은 로컬 단일 실행값이며, 기존 greedy의 전체 열거를 포함한 total을 확장성 근거로 쓰지 않는다.
- Semantica 도구를 사용할 수 없어 이번 결정은 이 문서와 `.scratch/swap-move-baseline/`에만 기록했다.

## 다음 실측 관문

1. Pi E1 exec 제어 연결 원인 확인 또는 실환경에서 검증된 대체 제어 경로(기존 문서의 선행 조건).
2. 새 설계 문서에서 5정책 집합, 교환·이동 규칙, 이 모듈 hash를 평가 전에 동결하고,
   수집기·감사기(`run_budget_placement.py`, `verify_placement.py` 등)의 정책 집합 계약을
   후속 작업으로 갱신한다. 이 계획 JSON을 그대로 실행 입력으로 쓰지 않는다.
3. 새 보정 후 두 블록을 완주하고, 같은 배치는 관측을 공유하며, 이전 부분 관측과 합치지 않는다.
