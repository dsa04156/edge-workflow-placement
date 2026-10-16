# 공통 관측·정책·HTTP 실행 연결 검증

검증일: 2026-10-16. [명세](../../.scratch/common-policy-runner/spec.md)에 따라 [PolicyRunner](../../experiments/e0/policy_runner.py)를 구현했다. **로컬 호스트의 실제 ORT CPU worker 두 프로세스를 통한 연결 검증**이다. 여러 장비·KubeEdge 가속기 성능 비교나 AnchorDL 재현은 아니다. 기존 E0~E3 실행기·동결 자료는 수정하지 않았다.

## 구현한 동작

`PolicyRunner.run()`은 현재 입력과 profile을 확인하고 worker health를 수집한 뒤 정책 callback 하나에 읽기 전용 snapshot을 전달한다. callback이 고른 세 Task의 위치를 실제 HTTP DAG에 적용하고 결과와 stage receipt를 대조한다.

| 경계 | 동작 |
|---|---|
| profile | ID·출처·입력 ID·source·model hash·공통 후보·비용표를 받는다. 다른 입력/source, 미등록 필드·비용 키·음수/비유한 비용을 거부한다. 미측정 비용은 `null`이다. |
| snapshot | 요청 ID/입력 ID/source/bytes/SHA256, profile, worker별 node/backend·service-object ready/generation·수신 monotonic 시각을 담는다. 전체 내용의 hash로 snapshot ID를 만들고 중첩 dict/list도 변경하지 못하게 한다. 가중치 메모리 상주는 `unknown`이다. |
| 정책 결정 | callback은 list/tuple 형태의 alias3개를 반환한다. 모든 Task가 공통 후보에 속해야 한다. 잘못된 반환값도 JSON으로 저장 가능한 오류로 남긴다. |
| 적용 전 상태 | T2 worker의 ready/generation을 재조회한다. 처음 관측과 달라졌으면 workflow 전송 전에 거부한다. 갱신된 snapshot으로 새 시도를 할 수 있으며 자동 재시도는 하지 않는다. |
| 실행 결과 | request/input/plan/stage/alias/node/backend, 최초 입력 hash·bytes와 간선별 hash·bytes 연결을 확인한다. worker가 보고한 수치 품질/실제 장치·준비 상세는 원 결과에 보존한다. |
| 시도 기록 | 성공·오류 단계/종류/이유, snapshot·계획·결과, 검증/관측/결정/계획검사/상태검사/HTTP 실행/receipt 검증의 시간을 반환한다. 실패 후에도 기록을 저장할 수 있다. |

정책 함수가 받는 public 호출 형태는 다음과 같다. `peers`와 `profile`은 호출자가 사전에 확인해 제공해야 한다. 아래는 API 사용 형태이며 새 실험을 실행한 명령이 아니다.

```python
with PolicyRunner(peers) as runner:
    attempt = runner.run(
        request_id=request_id, input_id=input_id, body=image_bytes,
        source=source_alias, profile=calibration_profile,
        policy_name=policy_name, policy=choose_plan,
    )
```

profile 최상위 필드는 `id`, `provenance`, `input_id`, `source`, `model_sha256`, `candidates`, `cost_ms`로 고정한다. `candidates`는 세 Task의 alias 목록이다. `cost_ms`는 기존 source 조건부 표와 연결할 수 있는 다음 형태다. `task`는 `0|alias` 등, `ingress`는 목적지 alias, 두 `edges` 표는 `from|to`, `service_prepare`는 준비할 worker alias를 키로 쓴다. 모든 등록 worker 조합의 비용 칸을 제공하며 모르면 `null`이다. profile 출처 문자열이 있다고 해서 실제 보정·품질 검증이 완료됐다고 판정하지 않는다.

이 연결부는 비용을 합산하거나 준비 비용을 상각하는 정책을 내장하지 않는다. 따라서 원문의 look-ahead·cache 의미를 임의로 정하지 않고 이후 확인한 정책을 연결할 수 있다.

## 실제 검증 결과

[다섯 slice의 red/green 기록](../../artifacts/common-policy-runner/tdd.json)과 [HTTP 테스트](../../experiments/e0/test_policy_runner.py)를 보존했다. 기존 승인된 HTTP 경계에서 한 slice씩 실패를 확인하고 구현했으며 전체 회귀 **17개 테스트**가 통과했다. 17개에는 기존·상속된 계약 검사가 포함되어 독립 실험17회나 새 시나리오17개를 뜻하지 않는다.

새로 확인한 다섯 동작은 다음과 같다.

1. 읽기 전용 snapshot으로 선택한 `A→B→A`가 실제 HTTP를 거쳐 dog의 hash와 Top-5를 유지한다.
2. T2 후보에서 B를 제외하면 B를 선택한 정책을 실행 전에 거부하고 B의 cold 상태를 유지한다.
3. 결정 중 B가 eviction되면 적용 전 검사에서 거부한다. 새 시도에서는 실제 CPU session 재준비가 요청 안에 들어가고 다음 요청은 warm 상태를 쓴다.
4. 다른 입력/source의 profile, 미래 source 필드, 정의되지 않은 비용표 필드를 실행 전에 거부한다.
5. 정책이 Python 객체 등 잘못된 계획을 반환해도 시도 기록을 JSON으로 저장할 수 있다.

[별도 시도 기록8건](../../artifacts/common-policy-runner/attempts.json)에서는 정상 dog warm/cold/reused와 camera 전달4건을 실행했다. [worker A](../../artifacts/common-policy-runner/worker-a.log)·[worker B](../../artifacts/common-policy-runner/worker-b.log)의 stage receipt12개와 응답 trace를 대조했다. 나머지4건은 제외 후보·상태 변경·입력 profile 불일치·잘못된 정책 출력의 **의도한 거부**다. 지연 표본에서 실패를 숨긴 것이 아니다. [검증 JSON](../../artifacts/common-policy-runner/smoke-verification.json)에 분모·오류 단계·환경·source hash·두 worker 종료를 기록했다.

환경은 Python3.12.3, ORT1.23.2, NumPy2.2.6, Pillow11.3.0이었다. 비용표는 **명시적인 synthetic interface fixture**다. CPU 실행은 실제 추론이지만 이 비용표로 정책 성능·입력별 최적해·NPU 이득을 평가하지 않는다. `local-a`/`local-b`는 같은 호스트의 worker 식별자다.

## 재현 명령과 비용 경계

프로젝트 루트에서 실제 실행해 통과한 회귀 명령이다. 모델·입력은 기존 hash 고정 경로를 사용하며 테스트가 만든 임시 worker 프로세스와 디렉터리를 종료/정리한다.

```bash
rtk proxy uv run --python /usr/bin/python3 --no-build --with numpy==2.2.6 --with onnxruntime==1.23.2 --with Pillow==11.3.0 python -m unittest discover -s experiments/e0 -p 'test_*.py'
```

`total_ms`에는 입력 검증, health 접속/조회, snapshot 생성, 정책 callback, 계획·적용 전 상태 검사, HTTP workflow, 응답 검증이 들어간다. 그 안의 NPU/CPU 준비 시간은 workflow 안에 포함되는 상세값이므로 다시 합산하지 않는다. SDK/Pod 시작·외생 eviction 제어·결과 파일 기록·runner 연결 종료는 제외한다. 이번 fixture의 callback 안 eviction은 상태 변경을 재현하기 위한 것이므로 그 대기 시간은 해당 검증 시도의 결정 시간에 들어간다. 이 기록으로 정책 계산시간을 추정하지 않는다.

runner는 순차 client 하나를 대상으로 연결을 재사용한다. 여러 정책에 같은 snapshot을 전달해 결정을 비교할 수 있는 입력 형태지만, **서로 다른 `run()` 호출은 health를 새로 관측**한다. 이번 검증에서 여러 알고리즘의 동일 상태 비교를 완료한 것은 아니다. 실제 정책 비교에서는 cache/queue 초기화·정책별 궤적·프로파일 보정/평가 분리를 별도로 고정해야 한다.

## 적용 경계와 다음 단계

- 적용 전 재조회는 관측된 ready/generation 변경을 검출한다. 재조회 뒤의 상태 변화, 프로세스 재시작에 따른 generation 초기화, 원자적 예약·동시성 경쟁은 해결하지 않는다. 서로 다른 worker의 health도 순차 관측이므로 같은 순간의 상태가 아니다.
- 입력/source/후보는 호출자가 제공한다. 실제 발생 위치·profile 측정 절차·품질 기준·runtime 검증의 진실성을 이 API만으로 증명하지 않는다. 허용 필드 제한은 실수로 미래 일정을 전달하는 경로를 막는 것이며 callback의 파일/네트워크/전역 변수 접근을 격리하지 않는다.
- 현재 준비 상태를 제공하는 CPU/NPU lifecycle 계약을 대상으로 한다. 이번 새 연결부의 실제 검증은 CPU에서 수행했으며 NPU/GPU의 새 실행 검증으로 세지 않는다. 기존 [E3 NPU Top-5 차이](e3-input-replication.md)는 그대로 유지한다.
- Link·driver·EdgeCore/CloudCore·운영 workload는 변경하지 않았다. 이번에는 클러스터 리소스를 생성하거나 EdgeCore를 새로 측정하지 않았다.

Context7은 월 quota 초과 응답으로 사용할 수 없어 GitHub MCP의 [CPython 공식 HTTP 문서](https://github.com/python/cpython/blob/v3.11.14/Doc/library/http.client.rst)를 확인했다. 응답을 끝까지 읽은 뒤 연결을 재사용하는 계약을 따른다.

[code-review 스킬](/home/jinuk/.agents/skills/code-review/SKILL.md)의 “confirm the fixed point resolves (`git rev-parse <fixed-point>`) and the diff is non-empty”는 현재 Git 저장소가 없어 충족하지 못했다. `rtk proxy git rev-parse --show-toplevel`은 exit128이었다. 따라서 Git 기준 두 축 리뷰 완료로 표시하지 않으며, 직접 source/명세 점검·실제 HTTP 회귀·receipt 대조를 검증 근거로 남긴다.

다음은 실제 보정값을 이 profile 형식에 연결하고 평가 전에 정한 품질/후보 조건으로 정책을 비교하는 것이다. **AnchorDL 원문 비용식·look-ahead·cache·baseline은 여전히 필요**하며 원문 없이 이를 추정해 구현하지 않는다. [재현 계약](../research/anchordl-reproduction-contract.md)의 미확인 항목은 유지한다.

후속 [E2 실측 표 연결](measured-policy-bridge.md)에서는 기존 원 정책을 같은 snapshot에 연결하고 대표10배치를 실제 ARM 엣지/Intel NPU 서버에 적용했다. 이는 과거 표의 결정·적용 호환성 검증이며 원문 정책이나 최신 profile의 성능 비교는 아니다.
