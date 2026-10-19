# 전체 phase 종료 시 native backlog 검증

2026-10-19T11:17:17.683844+00:00 UTC에 최종 run의 완료된 **33개 phase 전부**, phase별 마지막 **2개씩 총 66개** `drain-qdisc-*.stdout`을 독립적으로 검증했다. 원시 문자열로 보존된 `TCA_STATS`와 `TCA_STATS2/TCA_STATS_QUEUE`를 `ast.literal_eval()`로 읽었으며 기존 observer의 요약 `backlog_bytes`를 증거로 사용하지 않았다.

**66개 capture의 모든 qdisc에서 native backlog와 qlen이 0이었다.** 파싱 실패·누락·비영점·불일치 0건이다. 상세 per-phase/per-qdisc 값과 stdout·event·stderr·완료 ledger의 SHA256은 `backlog-native-audit-v2.json`에 있다. 재실행 가능한 분석은 `backlog_native_audit_v2.py`이며 v1 파서를 그대로 재사용한다. 기존 v1 artifact를 덮어쓰지 않았다.

이 중 **10개 capture**는 `observer="native-TCA_STATS-v2"`를 포함했다. 그 capture의 typed `qdisc_summary`를 각 handle의 원시 `TCA_STATS` bytes·packets·drop·overlimits·qlen·backlog와 대조했으며 모두 일치했다. 나머지 56개는 기존 observer가 생성한 원시 qdisc 속성에 근거한다. 이전 observer의 recursive summary가 pyroute2 nla slot을 놓치는 문제는 이 사후 검증으로 종료 시점의 실제 수치를 복원했다. 이전 실행기에 새 observer가 처음부터 적용됐다고 해석하지 않는다.

이 검증은 **phase 간 drain 검사 시점**의 queue가 비어 있었다는 근거다. phase 전체의 backlog가 0이었다거나 초과 부하 중 queue가 생기지 않았다는 뜻은 아니다. parent/child qdisc와 `TCA_STATS`/`TCA_STATS2`의 중복 필드는 독립 표본 또는 서로 다른 byte 수요로 합산하지 않았다. 이번 작업은 저장된 artifact 분석뿐이며 클러스터 설정·모델·추가 트래픽을 변경하지 않았다.
