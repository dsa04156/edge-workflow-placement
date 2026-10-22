# YOLO11 모델 규모 확대 비교의 근거와 통제 조건

작성일: 2026-10-22. research 스킬에 따른 좁은 공식 자료 조사다. **이 문서는 무거운 모델의 실행 결과가 아니다.** 모델 가중치를 다운로드하거나 구현·클러스터를 변경하지 않았다. Context7은 월 quota 초과로 실패했으며, 공식 웹 문서·GitHub MCP의 고정 소스·공식 release API를 확인했다.

## 권장 선택

첫 후속 비교는 **YOLO11n ↔ YOLO11l**로 한다. 같은 YOLO11 검출 계열·동일 640 입력에서 연산량 차이가 충분히 크고 기존 전처리·후처리를 재사용하기 쉽다. 필요하면 m을 중간 단계로 추가한다. x는 l의 실제 CPU 시간·메모리·timeout을 본 뒤 별도 단계로 둔다. 이는 실험 범위를 정하는 제안이며 l 또는 x의 현재 9개 노드 실행 가능성을 확인한 결과가 아니다.

공식 검출 성능 표의 640 입력 수치와 release 파일 metadata:

| 모델 | 파라미터(M) | FLOPs(B) | n 대비 FLOPs | 원본 .pt bytes |
|---|---:|---:|---:|---:|
| YOLO11n | 2.6 | 6.5 | 1.00× | 5,613,764 |
| YOLO11m | 20.1 | 68.1 | 10.48× | 40,684,120 |
| YOLO11l | 25.3 | 87.2 | 13.42× | 51,387,343 |
| YOLO11x | 56.9 | 195.3 | 30.05× | 114,636,239 |

파라미터·FLOPs 출처는 [Ultralytics YOLO11 공식 표](https://docs.ultralytics.com/models/yolo11/), bytes는 [Ultralytics assets v8.3.0 release API](https://api.github.com/repos/ultralytics/assets/releases/tags/v8.3.0)다. 비율은 공식 표 숫자로 계산했다. **13.42배 연산량은 13.42배 실측 지연을 뜻하지 않는다.** 파일 bytes도 ONNX bytes·실행 시 RAM·준비 시간과 다르다. API digest가 모두 null이므로 구현자가 실제 다운로드한 원본 bytes의 SHA256을 별도로 기록해야 한다. 공식 다운로드는 [n](https://github.com/ultralytics/assets/releases/download/v8.3.0/yolo11n.pt), [m](https://github.com/ultralytics/assets/releases/download/v8.3.0/yolo11m.pt), [l](https://github.com/ultralytics/assets/releases/download/v8.3.0/yolo11l.pt), [x](https://github.com/ultralytics/assets/releases/download/v8.3.0/yolo11x.pt)다.

기존 exporter와 같은 Ultralytics 8.3.220 commit `b35e516270349ea39ee9dd18d6e593012a589c46`의 [YOLO11 구조 YAML](https://github.com/ultralytics/ultralytics/blob/b35e516270349ea39ee9dd18d6e593012a589c46/ultralytics/cfg/models/11/yolo11.yaml)은 n/m/l/x를 같은 구조의 depth·width scaling으로 정의한다. 이 소스 주석에는 각각 6.6/68.5/87.6/196.0 GFLOPs로 적혀 있어 현재 공식 표와 소폭 다르다. 문서 표와 소스 주석의 계산 상태 차이를 숨기거나 혼합하지 않는다. 최종 실험은 실제 export 산출물 hash와 실행 측정을 기준으로 한다.

## 기존 실행 경로와 호환성 경계

현재 [export_yolo.py](../../experiments/multi-edge-sensors/export_yolo.py)는 `imgsz=640`, `opset=17`, `dynamic=False`, `simplify=False`, `nms=False`, `half=False`, CPU export를 사용한다. [models.py](../../experiments/multi-edge-sensors/models.py)는 RGB FP32 640×640 letterbox, conf=0.25, class-aware NMS IoU=0.7, 최대 300개 결과, ORT `CPUExecutionProvider`, 기본 intra-op=1/inter-op=1이다. 입력 영상 640×480와 모델 입력 640×640를 구분한다.

공식 [YOLO11 지원 표](https://docs.ultralytics.com/models/yolo11/)는 n/m/l/x 검출 모델의 export를 지원한다고 명시하며, [export 문서](https://docs.ultralytics.com/modes/export/)는 ONNX와 입력 크기·batch·dynamic·opset·simplify·NMS 설정을 설명한다. 다만 현재 export 문서는 최신 모델·옵션도 포함하므로 기존 8.3.220 실행에 최신 기본값을 가져오지 않는다. [고정 default.yaml](https://github.com/ultralytics/ultralytics/blob/b35e516270349ea39ee9dd18d6e593012a589c46/ultralytics/cfg/default.yaml)과 실제 동결 exporter 설정을 우선한다. 공식 ONNX 지원은 모든 엣지 장치에서 동일 수치·메모리·timeout으로 실행된다는 보장이 아니다.

현재 exporter는 입력 가중치 경로를 받지만 작업 파일명·설명·provenance URL을 `yolo11n`으로 고정한다. l 가중치만 넣으면 출처가 잘못 기록될 수 있으므로 모델 ID·원본 URL·원본 SHA256·파생 ONNX SHA256을 정확히 연결하는 변경이 먼저 필요하다. 기존 n manifest·artifact는 덮어쓰지 않는다. 모델 교체 후 원본 PyTorch와 ONNX의 동일 tensor 품질 대조, 공통 전처리 경로 대조, 양 아키텍처와 각 노드 calibration gate를 다시 수행해야 한다. n과 l의 검출 결과를 같아야 한다고 요구하지 않고 **각 모델 자체 reference**와 비교한다. 이 parity 검사는 COCO 정확도 평가와 다르다.

## 비교 계약 제안

1. **모델 크기만 먼저 바꾼다.** 5개 엣지 source 위치·9개 실행 후보·3개 workflow·V∥T→F DAG·MiniLM·F 로직은 유지한다. 모든 정책에 같은 모델을 주며 모델 선택 효과와 배치 효과를 섞지 않는다.
2. **같은 입력 bytes를 보장한다.** 이전 실행의 동결 RTSP-decoded PNG를 재사용하거나, 새 실행에서 n/l 모두에 같은 동결 PNG SHA256을 제공한다. RTSP 영상을 다시 생성했다고 bytes가 같다고 간주하지 않는다. 입력 ID·텍스트·전처리·conf/NMS·batch=1·FP32·ORT 버전·thread=1·CPU 요청/제한을 고정한다. 해상도나 동시 요청 수도 한꺼번에 올리지 않는다.
3. **variant별 보정→품질 후보 동결→별도 평가를 지킨다.** l용 실제 실행시간·PNG decode·전송·결과 envelope bytes를 다시 측정한다. l 검출 수가 달라져 출력 bytes도 달라질 수 있다. 각 variant 안에서는 모든 정책이 동일 후보를 사용한다. n/l 후보가 다르면 전체 결과를 보고하고, 모델 규모만 분리하려면 공통 후보에 제한한 보조 비교도 둔다.
4. **warm 지연과 준비 비용을 나눈다.** 모델 다운로드·배포·ORT session load·첫 추론·warm 추론·workflow 총 지연을 구분한다. 더 큰 모델의 memory peak·OOM·timeout·실패도 기록한다. 무거운 모델은 session 준비와 지속 실행의 비용 비중이 달라질 수 있다.
5. **정책 차이를 ms와 %로 함께 보고한다.** Task-fastest, network-aware greedy, exact DAG, default-scheduler를 동일 상태에서 비교한다. 같은 계획은 같은 관측을 공유한다. source-local/server-fixed는 진단용으로 유지한다. n/l 순서를 고정해 시간대 효과와 혼동하지 않도록 반복 block의 순서를 교차하고, 실제 추가 반복 수·시간 한도는 실행 전에 정한다.
6. **이 첫 단계의 한계를 유지한다.** CPU 공통 경로의 모델 규모 대조다. GPU/NPU, 여러 source의 동시 부하·queue, 실시간 RTSP 전체 지연은 별도 실험이다. 큰 모델에서 프레임 처리 시간이 200ms를 넘는다면 5fps 스트림의 frame drop/backlog를 조사할 후속 근거는 되지만 현재 순차 요청 지연만으로 실시간 처리량을 추정하지 않는다.

## 무엇이 의미 있게 달라질 수 있는가

다음은 **실험 가설**이며 이 조사에서 측정한 사실이 아니다.

- 무거운 V의 엣지/서버 연산 속도 차이가 더 커지면 영상 source와 떨어진 서버로 보내는 이익이 커질 수 있다. 검증 대상은 각 source의 `엣지 실행시간 - 서버 실행시간`과 실제 추가 이동 비용이다.
- 반대로 Task-fastest도 이미 가장 빠른 서버를 선택하면, V의 연산시간이 늘어도 DAG 공동 결정으로 줄일 수 있는 추가 비용은 비슷할 수 있다. 예를 들어 같은 5ms 절감이 기준 지연 50ms에서는 10%, 500ms에서는 1%다. 따라서 **무거운 모델일수록 DAG 최적화의 상대 이득이 반드시 커지는 것은 아니다.**
- 현재 구조는 무거운 V 하나, 가벼운 T와 F이므로 l로 바꾸면 V 지배적인 offload 사례가 될 수 있다. 이 결과는 연구에 유용하지만 여러 무거운 Task의 공동 배치 필요성을 자동 입증하지 않는다. 이후 별도의 DAG에서 복수의 실질적인 모델 stage와 큰 중간 표현을 검토할 수 있다.
- 동시 입력·서버 경쟁·GPU/NPU 가용성·모델 준비 상태를 추가하면 다른 배치가 이길 수 있다. 이 변수들을 첫 n/l 비교에 함께 바꾸면 원인을 분리하기 어렵다.

판정은 **더 무겁게 해서 이득을 크게 만드는가**보다, 모델 규모가 커질 때 정책별 배치가 실제로 달라지고, 그 차이가 연산·이동·준비 비용 중 무엇으로 설명되는가에 둔다. 이득이 줄거나 정책이 같은 배치를 선택하는 조건도 결과다.
