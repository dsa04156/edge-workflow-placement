# 계획 근거와 배정표

이 디렉터리는 새 AI 성능 실험 결과가 아니다.

- `20261019t090313z/nodes.json`, `pods.json`, `scheduler.json`, `version.json`: 읽기 전용 kubectl의 시작/종료 시각, 명령, 반환 코드와 분석에 필요한 선택 필드. 전체 Pod 명세·환경변수·secret은 저장하지 않았다.
- `20261019t090313z/allocated-resources.json`: 별도 describe 결과의 노드명/예약 자원 부분. 최초 snapshot과 완전히 원자적인 관측은 아니다.
- `20261019t090313z/simulation-audit.json`: e110d8b의 gzip 원자료 hash, 조건/시드/정책 수, 공유 trace·배치 consistency 및 보고서 산술 대조. 새 시뮬레이터 실행·추론 실측·통계적 유의성 재분석은 하지 않았다.
- `generate_schedule.py`, `pilot-schedule.csv`, `schedule-manifest.json`: experimental-design 스킬의 assign_factorial_runs를 사용한 pilot 초안. seed20261019; 3block×3regime×6정책=54slot. 배치가 같으면 공유 실행군을 연결하며 실제 실행 수는 달라질 수 있다.

이번에 확인한 생성 명령:

```bash
rtk proxy uv run --offline --with numpy --with pandas python artifacts/topology-profile-planning/generate_schedule.py
```

생성기는 기존 배정표를 덮어쓰지 않으므로 재생성 검증은 새 디렉터리의 스크립트 복사본에서 수행한다. 실제 모델 이미지·CPU reference·fork/join worker·네트워크 shaping은 이번 작업에서 실행하지 않았다. 전체 계획은 [실행 계획](../../docs/experiments/topology-profile-cluster-plan.md)을 따른다.
