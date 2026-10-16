# 공통 관측·정책·HTTP 실행 연결

Status: ready-for-human

## 범위

AnchorDL 본문과 독립적으로 준비할 수 있는 기존3Task chain의 정책 연결부를 구현한다. 특정 논문 알고리즘이나 새로운 최적화 정책은 추가하지 않는다. 원문 계약이 확보되면 해당 정책의 입력을 연결할 수 있도록 현재 요청·공통 비용표·후보·실제 서비스 객체 상태를 제공한다.

기존에 합의된 검증 경계는 실제 HTTP DAG다. 상태 관측→정책 callback→배치 적용→양끝 hash/실제 위치 receipt를 이 경계에서 확인한다. 미래정보 차단은 API가 제공하는 필드의 제한이며 정책 코드를 격리하는 sandbox가 아니다.

## 구현 계약

- 새 연결부는 `experiments/e0/policy_runner.py`다. 기존 E0~E3 실행기·동결 자료는 변경하지 않는다. CPU/NPU의 `DYNAMIC_LIFECYCLE=1` health 계약을 사용한다.
- 정책은 읽기 전용 snapshot 하나를 받아 세 alias의 계획을 반환한다. 요청 ID/입력 ID·bytes/hash·source, 사전 고정 profile ID/출처·모델 hash·후보·비용, 각 worker의 관측 시각·service-object ready/generation을 제공한다. 가중치 상주 여부는 `unknown`으로 남긴다.
- profile과 입력 ID/source/model hash를 대조한다. 허용하지 않은 profile 필드는 거부한다. 이 비용표의 정확성·품질 요구 충족을 새 연결부 자체가 입증한 것으로 보지 않는다.
- 정책의 계획을 공통 후보와 대조한다. T2 worker의 준비 상태/generation을 적용 직전에 재확인하고 바뀌었으면 해당 시도는 실행 전 거부한다. 이 검사는 원자적 예약이나 동시 요청 경쟁 해결이 아니다.
- 실행 결과의 요청/입력/계획/Task·node·backend·payload hash 연결을 검증한다. 실제 출력 품질 관측을 보존하며 임의 입력 제외·자동 fallback·재시도는 하지 않는다.
- 모든 시도는 성공 또는 오류 단계/이유·snapshot ID·계획·시각별 비용을 반환해 저장할 수 있게 한다. 종단 시간에 입력 검증·관측·정책·적용 전 상태 검사·HTTP 실행·receipt 검증을 포함한다. SDK/Pod 시작과 결과 파일 기록·연결 종료는 제외한다.
- runner 내 연결은 재사용하고 worker 간 전달은 기존 실행기를 따른다. HTTP 응답 본문을 끝까지 읽고 bounded JSON/응답 alias를 확인한다.

## 검증과 완료

실제 로컬 ORT CPU worker2개에서 입력 전달, 제외 후보 거부, cold→warm과 결정 후 상태 변경 거부를 한 slice씩 red→green 검증한다. fixture 비용표는 명시적 검증용 수치이며 실측 정책 성능으로 보고하지 않는다. 기존 전체 HTTP 회귀를 수행하고 source/로그·문서·검증 결과를 남긴다. 새 클러스터 실험은 이 연결부 검증의 완료 조건이 아니다.

Context7은 월 quota 초과 응답으로 사용할 수 없었다. GitHub MCP에서 CPython v3.11.14의 공식 `Doc/library/http.client.rst`를 확인했다. 공식 자료: https://github.com/python/cpython/blob/v3.11.14/Doc/library/http.client.rst .

## 완료 결과

읽기 전용 snapshot·profile/후보 검사·T2 상태 재확인·실제 HTTP 전달·실패 기록을 구현했다. 다섯 slice red/green과 전체17개 회귀를 통과했고 별도8시도 중 정상4건의 receipt12개·의도한 거부4건을 보존했다. 로컬 동일 호스트의 실제 CPU worker2개와 synthetic 비용표 검증이다. 원문 정책·NPU 성능·클러스터 재실행으로 보고하지 않는다. [실행 문서](../../docs/experiments/common-policy-runner.md)를 따른다.
