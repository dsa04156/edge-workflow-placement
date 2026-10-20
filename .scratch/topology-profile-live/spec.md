# 실제 모델 분기·합류 첫 실행

Status: ready-for-agent

Execution: completed — CPU reference60, edge/default-scheduler 두 배포24workflow/72receipt. [결과](../../docs/experiments/topology-profile-smoke.md). 네트워크·6정책 본 비교는 후속 범위다.

사용자가 2026-10-19 실제 실험 실행을 요청했다. 첫 수집 범위는 모델 자산/reference gate와 고정 세 노드 분산 실행 smoke다. 배치 정책 비교·네트워크 제약 파일럿 전체와 구분한다.

- 실제 JellyBean ResNet18 no-att + wav2vec2-base + 대응 MUTAN CPU batch1.
- CPU reference60쌍은 보정20/평가40, 이미지 분리 사전 목록을 사용한다. 입력 제외 금지.
- E0(dev0003) 이미지 source/vision, E1(dev0002) 음성 source/speech, C(ser0002) fusion.
- stage outputs는 vision/speech에서 fusion으로 직접 전달. source E0는 하나의 monotonic 시계로 완료를 잰다.
- first calibration pair2warmup; first5evaluation pairs2회,10measured. 닫힌 루프이며 고정RPS나 독립10환경으로 부르지 않는다.
- 분산 실행 전에6입력의 ARM feature allclose(rtol1e-4,atol1e-5)/ASR exact 품질 gate. 최종 answer ID exact/score abs1e-4;Top5순서는 별도 기록한다.
- task/node/Pod UID/device/model/input/output hash와3stage receipt를 대조한다. 실패/timeout/부분stdout·stderr·시작/종료event를 보존한다.
- 운영 설정은 변경하지 않고 소유 Pod/ConfigMap만 제거한다. 보호 workload 전후와 소유 리소스 최종0개를 확인한다.
- 구형 profile.py의 stdlib profile shadow를 피하도록 신규 실행 코드는 experiments/real-topology-live에 둔다.

테스트 경계: 실제 HTTP /health, /infer, branch→join→result. 모델 adapter는 실제 자산으로 CPU reference/분산 품질 gate를 통과해야 한다. 로컬 합성 adapter 회귀를 실제 AI 결과로 세지 않는다.

모델 초기화 보완: MUTAN은 공식 graph로 구성하고 unused skipthought pretraining 초기화를 건너뛴 뒤56state항목을 strict load/torch.equal 검증한다. vocabulary는 공식 train input·원 전처리로 재구성했으며 독립적인 원 훈련 mapping hash는 없다. 그 한계를 보고한다.
