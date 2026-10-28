# VLM 반복 실측 및 슬라이드 자료

사용자: SD I/O 관측 저속 다운로드 후 실제 실험, 슬라이드용 결과, Git 커밋·푸시. 기존 4MiB/s 상한 유지. Mobilint는 다운로드 대기 중 해제했고 준비 완료 뒤 자원이 비어 있으면 다시 사용한다. 외부 작업을 선점하지 않는다.

## 설계와 수용 기준
- 기존 모델·입력·runtime·단계별 가속기 실행 계약을 재사용한다. GPU BF16 / Mobilint W4V8의 서로 다른 구성임을 명시한다. 품질 점수와 SLO/goodput은 범위 밖이다.
- 기존 qualification(입력 4개/arm)을 먼저 통과해야 한다. 해당 관측은 워밍업으로 보존하고 반복 통계에서 제외한다.
- plan.json: 고정 입력 4개 x 10 round x 2 arm = 80 sequential calls. 같은 (round,pair)는 같은 입력을 두 arm에 한 번씩 실행하고 arm 순서만 seed 20261028의 permuted blocks로 결정한다. 전체 계획의 해시와 실제 선택 arm 계획을 저장한다.
- optional --arm는 선택 arm의 40행만 실행하며 별도 단일 arm 관측으로 명시한다. 다른 실행의 관측과 paired comparison으로 섞지 않는다.
- 같은 Pod 내 반복 측정이다. 독립 장치·데이터셋 표본이 아니며 효과크기·독립 반복 수 근거가 없으므로 검정력/유의성 주장을 하지 않는 예비 기술통계다.
- 새 benchmark_live.py/test_benchmark_live.py만 구현한다. 기존 live.execute/call_arm/identity/receipt/occupancy/UID cleanup을 재사용한다. 기존 소스·동결 산출물 변경 금지.
- release는 기존 코드·동결·준비/PVC/프로파일 계약과 새 collector 소스·계획 해시를 묶는다. 불일치는 배포 전에 거부한다.
- qualification 후 fresh owned Pod identity 및 readiness, queue completed baseline을 확인한다. 반복마다 per-arm sequence가 엄격히 증가하고 최종 completed delta가 일치해야 한다. Worker는 request_id 응답 캐시가 없으며 요청마다 sequence를 증가시켜 실제 stage.run을 실행하는 기존 구현이다.
- 각 응답을 measurement_id/round/pair와 함께 benchmark-ledger.jsonl에 flush/fsync한 후 다음 호출을 시작한다. 입력/출력/hash/UID/device receipt 검증을 유지한다. 첫 실패에서 중단하고 failure/unattempted/partial timing을 보존한다.
- 중단·실패 포함 모든 경로에서 UID 정리·부재 검증·보호 workload 전후 snapshot을 보존한다. CPU neural fallback/edge streaming/외부 자원 변경 금지.
- 원시 결과: collector/worker elapsed, host CPU, generated tokens(존재 시), 실패/미실행. 기술통계는 p50/p95(관측 분포), 입력별 분포, serial service rate(success / sum call time), 전체 wall elapsed를 각각 구분한다. 포화 처리량으로 부르지 않는다.
- 검증은 mock로 계획/승인 결합·중복 sequence 거부·첫 실패 중단·부분 결과/정리 보존을 확인한다. 실제 가속기 실험은 coordinator만 수행한다.

## 산출물
원시 실측 JSON/CSV, 재현 명령과 제한, 슬라이드용 PNG/SVG와 한국어 요점. 결과가 실패하더라도 실패 원인과 검증 범위를 그대로 보고한다. 큰 모델·wheel·tar·비밀값은 Git 대상이 아니다. 기존 다른 변경은 보존하고 이번 파일만 커밋·푸시한다.

## 설계 도구
experimental-design의 randomization.py block_randomization(n=80, arms=[cuda,mobilint], block_size=2, seed=20261028)를 사용했다. Python 3.11.15, NumPy 2.2.6, pandas 2.3.3의 기존 로컬 분석 환경에서 실행했다(스킬 권장 환경 3.12+와 다르며 실제 계획 배열/hash를 보존한다).

출처: Timothy Kassis, Vinayak Agarwal, Yuhuan He, Darshil Patel, Aubrey M. Brueckner (2026), Scientific Agent Skills: A Library of Procedural Knowledge for Research Agents. https://doi.org/10.48550/arXiv.2609.00065 (2026-10-28 원문 메타데이터 확인). 이 출처는 사용한 설계 도구에 관한 것이며 가속기 실험의 선행 성능 근거가 아니다.
