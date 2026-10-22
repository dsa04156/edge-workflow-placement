# 호스트 검증 기록

- 신규 비교 테스트: `rtk proxy python3 -m unittest discover -s experiments/real-topology-live -p test_baseline_comparison.py` → 33 passed.
- Developer 전체 live 테스트 보고: 65 passed. 호스트는 신규 33개를 직접 실행했으며 전체 65개를 다시 실행한 것으로 표시하지 않는다.
- 실제 최종 diff 확인: 새 collector의 전체 Pod 자원 조회만 allowlist projection. 기존 capacity 계산 경로를 유지하며 env/args/annotations를 원문 저장하지 않는다.
- 독립 검토 2회, 잔여 P2 최종 수정은 호스트 검증. 관련 보고서는 review-spec*.md, review-standards*.md, final-fix-report.md.
- 최종 worker 실제 모델: 557f9a15-fbea-4fe7-9c95-68e6baff12d8.jsonl의 assistant.model = claude-opus-5-5. 앞선 구현·검토 provenance는 orchestration.md와 각 worker 보고서를 따른다.
- 실측: wan-20261021t094522. 실행 결과·감사 결과는 수집 종료 후 추가한다.

## 재개 실행과 진단 검증

- 새 run wan-20261021t101203: 품질108/108, 평가9/9, 워밍업 포함12/12 성공. 평균 E2E는10Mbps 서버718.3947ms,2Mbps 서버1427.9055ms,2Mbps 영상 엣지934.5071ms. 원자료 직접 집계.
- 최종 guard(False) sink-e0 exec EOF3회로 종료코드1. cleanup remaining_owned0/errors[]. 검증기 exit1/status partial이며 보호 snapshot7개 변화와 종료/최종 map 누락 등을 그대로 보존.
- Opus 진단 replay를 호스트도 실행해8/8 PASS. 런타임 소스와 의존 파일 hash는 실행 동결본과 모두 일치. 구현 수정 없음.
- 실제 Opus transcript 2b11ae05-acdf-4e1b-9e1b-8788f96101b9.jsonl의 assistant.model=claude-opus-5-5 확인. claude.ai/firstParty Max 인증 확인, provider override 환경변수 없음. API/model fallback 없음.
- 결과: docs/experiments/scheduling-baseline-smoke-20261021.md. 추가 실측 없이 부분 결과로 정리.
