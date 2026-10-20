# 서버 슬롯 실험: 제어 응답 유실 복구 검증

2026-10-20. 기존 `wan-20261020t053336`의 마지막 unblock 명령은
`error sending request: ... unexpected EOF`로 중단됐다. 원격 명령의 실행
여부를 stderr만으로 판단할 수 없으며, KubeEdge EOF의 근본 원인은 미확정이다.

## 검증한 보완

네트워크 제어 명령에만 동일 Pod의 실행 기록을 붙였다. Pod UID·명령 소스·입력·
operation ID를 결합하고, 잠금 아래 시작 기록을 먼저 만든다. 완료한 명령은
결과 stdout을 저장한다. 전송 EOF 뒤 같은 operation으로 최대 두 번 결과를
회수할 수 있다. 완료 기록이 있으면 실제 변경을 반복하지 않는다. 시작했지만
완료하지 못한 기록, 실패 기록, 다른 UID/명령은 재실행하지 않고 중단한다.

이 보완은 추론 재시도가 아니다. 커널 설정의 현재 상태도 완료 기록으로
대체하지 않는다. 기존 차단 negative probe·relay 연결·직접 연결 복구·
실제 qdisc backlog 확인을 계속 실행한다. process/transport 실패 범위의
검증이며 machine power loss·파일시스템 손상 내구성은 검증하지 않았다.

## 검증 결과

- 수정 전 수집기의 네트워크 제어 경로에 pre-dispatch EOF를 주입하자
  `001-restore-control failed`로 중단되는 red 테스트를 확인했다.
- 수정 후 제어 테스트 8개 통과. 실제 로컬 child process가 변경을 완료한 뒤
  응답을 버리는 경우에는 결과가 회수되고, 일부 변경 뒤 오류 또는 프로세스
  종료가 발생하면 재실행을 거부했다. 동시 결과 회수도 한 번만 변경했다.
- 기존 전체 회귀21개 통과 후 결합 경계 테스트2개를 추가했고, 영향받는
  제어 테스트8개와 최종 전체 회귀23개를 통과했다.
- 클러스터 진단 `wan-20261020t081744`: 소유 helper Pod에서 차단·복구 3회,
  두 ARM Pod의 완료 block/unblock 명령 재사용 4회 통과.
  `control-diagnostic.json`의 복구 확인 true, `cleanup.json`의
  `remaining_owned=0`, `errors=[]`.
- 진단의 실제 소스는 해당 run의 `collector-source/`를 따른다. 진단 뒤
  테스트 가능한 공통 `execute_control` 메서드로 연결부를 추출했으며,
  실제 child process/응답 유실 결합 테스트를 추가했다. 이전 진단을
  그 뒤 소스로 실행했다고 주장하지 않는다.

실행 명령:

```bash
rtk proxy python3 -m unittest discover -s experiments/real-topology-live -p test_control_receipt.py -v
rtk proxy python3 -m unittest discover -s experiments/real-topology-live -p 'test_*.py' -v
rtk proxy python3 -u experiments/real-topology-live/probe_control_receipts.py
```

## 코드 검토

기준 `992b85e`에서 Standards/Spec 별도 검토를 수행했다. Standards는 hard
violation 0, directory fsync가 없어 기계 장애 내구성을 주장하지 말라는
비차단 한계 1건이었다. 위 해석 범위에 명시했다. Spec은 수집기+실제 child+
응답 유실을 결합한 테스트가 빠졌다는 1건을 발견했다. 추가 후 재검토는
남은 코드 finding 0이었다. 코드 검토는 실험 결과 감사를 대신하지 않는다.

## 후속 수집의 경계

새 전체 수집은 원래의 두 시간 블록·10/2Mbps·서버 슬롯 2개·평가 입력
split7–12·seed20261022를 유지한다. 기존 부분 측정과 합치지 않고 새 연산·
전달 보정을 한다. 이 입력은 과거 부분 결과에서도 사용됐으므로 새 holdout
입력이나 독립적인 확증 실험으로 부르지 않는다. 새 결과가 같은 계획을
선택하거나 차이가 줄어도 그대로 보고한다.

Semantica 도구 호출이 지원되지 않아 이번 결정은 로컬 명세와 문서에 남겼다.

후속 전체 수집과 추가 진단은 연속EOF로 중단됐다. 위 검증을 제어 연결의
신뢰성 보장으로 해석하지 않는다. [최신 결과와 미완료 범위](budget-completion-attempt.md)를 따른다.
