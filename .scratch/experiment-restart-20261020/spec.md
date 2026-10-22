# 연결 복구 후 실험 재개

사용자 요청: 장비 등록 이후 실험을 다시 시작한다.

1. 기존 CPU E0=dev0003, E1=dev0002, C=ser0002의 Ready와 제어 진단을 확인한다. NotReady 노드는 사용하지 않는다.
2. 제어 진단이 통과하면 기존 서버 슬롯2개·10/2Mbps·두 시간 블록 pilot을 새 보정부터 실행한다. 기존 부분 결과와 합치지 않는다.
3. 기존 run_budget_placement.py를 그대로 사용한다. 실행기 파일 hash는 이전 wan-20261020t082011과 일치했다. 평가 입력 재사용이므로 새 holdout 확증으로 부르지 않는다.
4. 실패·부분 출력·cleanup을 보존하고, 두 블록 완료와 품질·정책 매핑 검증을 구분한다.
5. 신규 Orin/Pi는 클러스터 등록을 마쳤지만 이 기존 CPU 실행기의 후보가 아니다. GPU adapter/동일 품질 검증 이전에 GPU 성능 결과로 보고하지 않는다. 신규 구현은 상위 AGENTS의 Claude 구독 solo 위임 계약을 따른다.

제어 진단: artifacts/topology-profile-live/wan-20261020t114454.
Semantica 검색은 unsupported call로 실패했다.
