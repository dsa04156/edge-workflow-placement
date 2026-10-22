# Sense HAT 실제 원본 입력 파일럿

Status: in-progress
사용자 요청: 기존 Sense HAT 센서 데이터를 사용해 실제 워크플로를 실행한다. 작은 실험 우선, 기존 센서·EdgeAI 서비스·과거 artifact 보존. 대규모 반복·신규 알고리즘 우월성 주장은 하지 않는다.

## 수용 기준과 범위

1. 실제 물리 Sense HAT 원본의 장치UID/항목/노드/원본 측정시각/설정과 수신 신선도를 확인한다. 등록이나 Pod Running만으로 수집 성공으로 표시하지 않는다.
2. 기존 영상·음성 fixture와 분리된 새 artifact로 저장하며 원본 hash와 조회 구간을 기록한다. 실시간 수집과 실제 과거 자료 재생을 구분한다. 서버 API 조회를 센서→결과 E2E로 보고하지 않는다.
3. 원본 시계열의 작은 학습형 예측 워크플로를 연결한다. 최소 기준은 시간창 특징→학습한 회귀 예측→실측 대비 오차/결과 기록. 환경 채널은 개별 시계열로 처리해 채널간 동시 측정을 가정하지 않는다. 물리 의미 없는 padding·반복 연산 부풀리기 금지.
4. 학습/보정/평가를 시간순 분리하며 미래값을 예측 입력에 넣지 않는다. 값의 지속 예측을 품질 기준선으로 두고 개선이 없으면 그대로 보고한다. 이상 탐지 정답이나 장치 고장 판정으로 표현하지 않는다.
5. source는 dev0003. 가능하면 기존 broker의 qualified 원본을 같은 엣지의 별도 소비자로 받아 짧은 실제 수신→실행 검증을 한다. 이력이 필요하면 학습에만 사용하며 저장된 이력으로 실행한 단계는 재생으로 표시한다.
6. 먼저 native 네트워크의 제한된 end-to-end 연결 검증을 한다. 기존10/2Mbps 영상 실험과 성능을 합치지 않는다. 실제 기본 스케줄러 및 단순 프로파일 배치 비교는 실행 호환성과 원본 접근 계약을 확보한 뒤 같은 입력/품질/자원으로 제한한다. 동일 배치는 관측 공유.
7. 소유 신규 리소스만 생성/정리, 기존 mapper·센서 설정·broker·공유 네트워크 변경 금지. 이벤트 시작/종료·실패·부분 stdout/stderr 보존. 주요 구현은 Opus5.5 구독, 독립 Astra Spec/Standards 검토 최대2회.

## 구현 경계

새 experiments/sensehat-placement/와 대응 .scratch/sensehat-placement/만 구현 소유. 기존 실험 코드·EdgeAI 소스는 참조만. 공개 CLI/입력-출력 변환/HTTP 실행 및 검증기 경계에서 실제 데이터 누락·미래값 누출·hash/노드 불일치를 검증한다. 기존 공개 경계의 TDD 계약을 재사용한다.

## 현재 상태

Sense HAT mapper는 dev0003의 /dev/i2c-1을 사용한다. 원본 이력 저장 API/InfluxDB 존재를 확인했으며 최신 데이터 접근은 Opus discovery task 진행 중이다. 정확한 입력 schema·구간·실행량은 데이터 경로 검증 뒤 별도 구현 계약으로 동결한다. 아직 학습/평가 실험은 시작하지 않았다.

## 동결한 첫 실행 범위

구현 계약은 implementation-task.md. 최근15분의 실제 원본 중 temp_humidity/humidity/pressure3개를 개별 처리한다. 각900행의60/20/20 시간순 구간,8개 과거값 평균을 입력으로 한 최소제곱 회귀와 마지막값 지속 예측을 대조한다. n=3/채널의 실제 클러스터 실행은 연결 검증 예산이지 검정력 계산에 의한 표본 수가 아니다. 동일 원본을 dev0003에서 재생해 엣지/서버/혼합/기본스케줄러 선택 배치를 비교한다. 이는 앞선6정책 비용최적화 본 비교의 재현이 아니라 실제 센서 입력의 첫 고정배치 진단이다. 원시 센서→아카이브 수집 경로는25초 MQTT 수신으로 따로 검증한다.

실행 순서는 seed20261024로 고유배치별 동결하며 한 블록 내 같은9입력을 사용한다. 겹친 학습/평가창이나 여러 정책의 같은 배치 관측을 독립 표본으로 세지 않는다. 성능은 기술 통계, 품질은 채널별 단위의MAE/RMSE를 보고하고 다른 단위를 합산하지 않는다.

## 절차·API 출처

experimental-design 스킬의 단위·블록·관측 공유 원칙을 적용했다. Kassis, T., Agarwal, V., He, Y., Patel, D., & Brueckner, A. M. (2026). Scientific Agent Skills: A Library of Procedural Knowledge for Research Agents. arXiv:2609.00065. https://doi.org/10.48550/arXiv.2609.00065 (2026-10-21 공식 arXiv의 최신v2 확인; 절차 출처이며 실험 타당성 보증이 아님).

회귀 API는 Python 공식 statistics.linear_regression 문서(https://docs.python.org/3/library/statistics.html#statistics.linear_regression)와 현재 실행 인터프리터 지원을 따른다. Context7은 월간 quota 초과로 응답해 공식 문서로 보완했다. GitHub MCP로 NumPy 공식linalg 구현을 확인했으나 새 패키지를 설치할 필요가 없는 표준라이브러리 단순회귀를 선택했다. 데이터 경로는 EdgeAI 공식 로컬소스/API를 discovery-report.md에서 연결한다.
