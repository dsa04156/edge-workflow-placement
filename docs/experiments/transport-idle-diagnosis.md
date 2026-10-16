# 중첩 HTTP 전달의 idle 연결 재사용 오류

확인일:2026-10-16. 최신 보정 실험의 첫 run `20261016t101650429037`은 평가 전에 중단했다. [보존한 원자료](../../artifacts/fresh-placement-attempt1/run.json), [오류](../../artifacts/fresh-placement-attempt1/failure.json), [client stderr](../../artifacts/fresh-placement-attempt1/calibration-b0-intel_npu.stderr), [정리](../../artifacts/fresh-placement-attempt1/cleanup.json)를 따른다.

## 증상과 원인

ARM source 보정256회 뒤 NPU-server source의157번째 요청에서 `RemoteDisconnected: Remote end closed connection without response`가 발생했다. 전체413시도 중412protocol통과·1오류였고 성공한 보정 중 Top-5 불일치28회는 별도 품질 관측이다. 이 오류는 품질 필터나 NPU compile 오류가 아니다.

실패 요청은 coffee의 `ARM→NPU 서버→ARM`이었다. client 로그의 같은 source 내 경로 이력에서 첫 ARM→NPU 전달 연결은4.294557초 전에도 사용됐지만, 그 안의 NPU→ARM 연결은26.2648초 동안 idle이었다. 기존 HTTP handler는 idle15초 뒤 연결을 닫는데 호출자는 전달용 연결을 기간 제한 없이 보관했다. 첫 실행은 오류 시 worker의 최신 stderr를 별도 수집하지 못했으며 이를 확보했다고 기록하지 않는다.

외부 네트워크·NPU를 제거하고 실제 CPU worker2개/dog 입력으로 범위를 줄였다. 처음에는 client 연결만 유지하고 전달 연결들을16초 쉬게 해 재현했다. 이어 A→B→B를4초마다 실행해 **A→B는 계속 사용하고 B→A만16초 쉬게** 범위를 더 좁혔다. 첫 실행의 동결 executor에서도 같은 workflow 단계의 RemoteDisconnected가 재현됐고, 수정 executor는 바깥 연결을 재사용하면서 안쪽 연결만 새로 열어 통과했다. [더 좁힌 red/green 기록](../../artifacts/fresh-placement/idle-isolation-tdd.json)을 보존한다. 임시 재현 디렉터리에 corpus 경로가 없어0개 테스트로 종료한 준비 오류1건도 성공/실패 재현과 구분해 기록했다.

## 수정과 회귀

[dag_profile.py](../../experiments/e0/dag_profile.py)는 전달 연결의 마지막 응답 완료 monotonic 시각을 기록한다. idle10초 이상이면 닫고 POST 전송 전에 새 연결을 만든다. peer timeout15초보다 여유를 두며 짧은 간격의 연결 재사용은 유지한다. 이미 전송한 POST를 자동 재전송하지 않는다. `next_connection_reused`를 trace에 남긴다.

[실제 HTTP 재현 테스트](../../experiments/e0/test_idle_forwarding.py)는 수정 전 실패하고 수정 후 같은 조건에서 통과했다. 이 수정은 nested idle 연결 문제를 다루며 임의 네트워크 장애·프로세스 재시작·원자적 요청 중복 방지를 해결했다는 뜻은 아니다.

전체 회귀에서는 별도로 테스트 fixture의 `Address already in use`1건이 발생했다. peer 포트를 모두 예약한 채 해당 서버를 시작할 때만 예약을 풀도록 보완한 뒤 전체38개가73.540초에 통과했다. [red/green·실패 회귀·최종 회귀 출력](../../artifacts/fresh-placement/tdd.json)을 보존한다. 이후 위 안쪽 연결만 쉬는 조건으로 테스트를 강화해, 과거 executor는20.481초에 의도한 실패, 수정 executor는20.537초에 통과했다. 런타임 소스는 전체 회귀 뒤 바뀌지 않았고 추가 검증은 영향받는 HTTP 테스트만 실행했다. 포트 보완은 로컬 테스트 준비에 한정되며 클러스터 비용표를 바꾸지 않는다.

```bash
rtk proxy env PYTHONPATH=experiments/e0 uv run --python /usr/bin/python3 --no-build --with numpy==2.2.6 --with onnxruntime==1.23.2 --with Pillow==11.3.0 python -m unittest test_idle_forwarding.IdleForwardingHTTP.test_nested_route_survives_idle_without_retrying_the_post
```

첫 run은 [별도 디렉터리](../../artifacts/fresh-placement-attempt1)로 보존하고 소유 리소스 잔여0·임시wheel14개 정리를 확인했다. 새 버전은 입력·모델·품질 규칙·seed·표본 수를 바꾸지 않고 보정부터 시작한다. 과거의 동결 ConfigMap은 수정하지 않았다. 실행 경로가 바뀌므로 첫 부분 보정과 새 보정/평가를 합치지 않는다. 수집 스크립트는 이후 오류가 나면 worker 로그도 삭제 전에 저장하도록 보강했다.
