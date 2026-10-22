# 연결 복구 후 실험 재개

2026-10-20. 사용자의 실험 재개 요청으로 기존 CPU 파일럿을 다시 시작했다.

- 제어 진단 `wan-20261020t114454`: 차단/복구 3회, 완료 기록 재조회 4회 통과. restored_connectivity=true. 소유 리소스 잔여0, cleanup 오류0.
- 새 수집 `wan-20261020t114647`: `rtk proxy python3 -u experiments/real-topology-live/run_budget_placement.py` 실행. 이 기록 시점에는 실행 중이며 완료/성능 결과는 아직 없다.
- 기존 실행기 파일 hash는 이전 `wan-20261020t082011`과 동일하다. 새 보정, 10/2Mbps, 방향별20ms, 서버 작업 슬롯2개, 두 시간 블록을 사용한다. 이전 부분 결과와 합산하지 않는다.
- 대상은 현재 Ready인 dev0003/dev0002/ser0002의 CPU이며, 새 Orin의 GPU 등록 성공을 모델 실행 검증으로 취급하지 않는다. GPU 확장은 별도 adapter/품질 gate가 필요하다.
- 실험은 이종 ARM/x86 CPU의 정적 배치 pilot이다. 새 holdout 확증/온라인 재배치 순이익/새 알고리즘 우월성을 의미하지 않는다.

원자료와 명령별 시작/종료·실패·정리 기록은 각 run 디렉터리를 따른다. Semantica는 도구 검색 오류로 기록하지 못했다.

사용자의 WireGuard 제거 요청에 따라 wan-20261020t114647을 보정 시작 단계에서 중단했다. cleanup 잔여0·오류0. 이 실행은 완료 결과가 아니다. 자세한 제거 결과는 wireguard-removal-20261020.md를 따른다.
