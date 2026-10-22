# Sense HAT 실제 데이터 경로 조사와 제한된 원본 표본 확보 (2026-10-21)

작업: task_0fb679cc56c9 / dispatch ctx_546a8139b883. 이 문서는 배치 실험이 아니라 **데이터 경로 조사 결과**다.
증거: `artifacts/sensehat-placement/discovery-20261021/` (모든 파일 sha256은 `SHA256SUMS`).

## 0. 결론 요약

| 항목 | 결과 | 수준 |
|---|---|---|
| 11개 채널 신원·단위·노드·프로토콜 | 확인 (§1) | 실제 클러스터 조회 |
| 2회 읽기, 약 10초 간격 freshness | 11개 채널 모두 seq +10~11, observedAt +10.0~11.0 s 진행 (§3) | 실제 실행 |
| 원본 이력 15분 | 채널마다 900행, truncated=false, QUALIFIED 100%, seq 끊김·중복 0 (§4) | 실제 실행 |
| 새 측정값 스트림 (MQTT) | 익명 subscribe만 사용, 25초 동안 qualified topic 11개 × 25 msg, seq 연속 (§5) | 실제 실행 (ser0001 → dev0003 broker) |
| dev0003 consumer 실행 가능성 | 가능해 보이나 **Pod 배포·실행은 하지 않음** (§7) | 근거만 확인 |
| 센서 window 추론 모델 자산 | 없음. 기존 ONNX 자산은 이미지 계열뿐 (§8) | 확인만 함 |

**라이브 캡처와 재생은 다르다.** `mqtt/live-25s.raw.txt`만 현재 시점의 라이브 수신(ser0001에서 subscribe)이다. `history/*.json`은 InfluxDB `sensor-originals`에 저장된 **과거 물리 측정값의 원본 문서**다. 이것을 consumer에 다시 흘리면 그건 실제 측정값을 **재생(replay)**한 것이지 라이브 센서 경로가 아니다. 재생할 때 전송 시점과 지연을 원래 값처럼 표시해서는 안 된다.

## 1. 채널 신원 (`reads/read-2.json` kube projection, 10:51:35Z)

공통 정보: namespace `edgeai-devices`, node `etri-dev0003-raspi5` (arm64), protocolName `edgeai-sensehat`, configData `{deviceId: sensehat-001, devicePath: /dev/i2c-1, timeoutMs: 4000}`, collectCycle 1000 ms, reportCycle 5000 ms, Device `metadata.generation`=2. 상태 정보는 Device.status가 아니라 별도 `DeviceStatus` CR(ownerReference=Device)에 있다. 데이터 저장소 API의 `deviceUid`는 K8s Device `metadata.uid`와 같다.

| property | deviceUid (API path uid) | unit | DeviceStatus gen | configSha256 (앞 12자) | sessionId | 15분 값 범위 |
|---|---|---|---|---|---|---|
| temp_humidity | caf05dfa-4181-4365-a0aa-77d3fce2e893 | °C | 59450 | 05a9e214aca4 | dmag2zeaemva | 43.04–45.36 |
| temp_pressure | d7fd345e-83bd-4376-800c-6b01897c7356 | °C | 59448 | 240c265ae04b | dmag2zeaemv8 | 39.39–41.44 |
| humidity | 57ba9fd2-9bdc-499b-a2f8-67d276eded77 | %RH | 59446 | 31af6a79f08f | dmag2zeaemv9 | 21.85–25.38 |
| pressure | f0a6d8bc-c74c-420b-bda4-49b0dc1fd3b8 | hPa | 59448 | 41148a969786 | dmag2zeaayrj | 1024.51–1024.78 |
| compass | 2b1ee4d0-f402-4e3d-828d-bd62c77c31a4 | ° | 59449 | d049f3039b1f | dmag2zeaayrl | 121.06–146.36 |
| pitch | 0b892da0-4864-40ab-9fe1-97b7882ed5d3 | ° | 59450 | 2d9eca758e5a | dmag2zeabg9o | 39.63–45.80 |
| roll | c81a558a-fb81-4c96-80b8-40ac7a3195ff | ° | 59446 | e34c84a5c18f | dmag2zeaemv6 | 13.87–77.51 |
| yaw | 05bdb8db-9ccc-4048-8abb-3ed598e5fcf2 | ° | 59450 | 390dd5fd557e | dmag2zeaayrk | 121.06–146.36 |
| gyro_x | b0db7c57-3a38-4775-afac-187dad68d429 | rad/s | 59452 | 4f2442d93f4d | dmag2zeaemvb | 0.017–0.112 |
| gyro_y | ab7bec1e-590f-4ef8-9332-a0e21245ace1 | rad/s | 59447 | d81ffdda878a | dmag2zeaemv7 | 0.0025–0.168 |
| gyro_z | 837f9f68-9792-48e8-9602-5aecfbfd2cf3 | rad/s | 59445 | ee7a789e069f | dmag2zea1467 | −0.0058–0.0026 |

- 채널마다 `sourceGeneration`이 다르며(각 Device visitor), 전체 값은 `reads/*.json`에 있다. 15분 창 안에서 config/sourceGeneration은 채널마다 하나뿐이었다.
- **compass와 yaw는 같은 값이다.** `edgeai/mappers/hardware/read_sensehat.py`가 `"compass": yaw`로 만든다. 독립 물리 채널은 10개다.
- temp_humidity가 43–45 °C로 높게 나온다. 보드/CPU 발열 영향일 가능성이 있으며 실내 기온으로 해석하면 안 된다(원인 미검증).
- 15분 동안 roll은 13.9–77.5°로 크게 변했다. 보드가 움직였는지는 확인하지 않았다.

## 2. 측정·전달 경로 (1차 소스 `/home/jinuk/codex-work/edgeai` @22dfefe)

1. `mappers/hardware/read_sensehat.py`가 `--interval 0.2`(`mappers/internal/driver/hardware.go:108`)로 실행된다. RTIMULib로 I2C를 읽어 5 Hz frame 하나에 11개 값과 `origin=time.time_ns()`를 함께 담는다.
2. Mapper Device 인스턴스(채널마다 하나)는 collectCycle 1000 ms마다 새로 들어온 최신 frame만 snapshot한다(`hardware.go` BeginCycle). 따라서 **저장·전달되는 값은 5 Hz source를 1 Hz로 간추린 것**이다. 5 Hz 전량은 어느 경로에도 노출되지 않는다.
3. `ReadObservation`: `/dev/i2c-1`이면 observedAt=origin/1e6 (ms)이고 `timeBasis=SOURCE`다.
4. `mappers/internal/runtime/panel.go:300-345`는 같은 broker(127.0.0.1:1883, 노드 NanoMQ)에 두 번 publish한다.
   - 원본 DataModel: `edgeai/devices/<device>/<property>` (`TimeStamp`, `Value` 문자열)
   - qualified: `edgeai/observations/v1/edgeai-devices/<device>/<property>`, JSON 키 `apiVersion=edgeai.observation/v1, namespace, deviceName, property, sourceGeneration, configurationSha256, observedAt(ms), receivedAt(ms, dev0003 시계로 publish 직전), timeBasis, sessionId, sequence, type, unit, value`
   - `sequence`는 Device 인스턴스(=채널)마다 독립이다. 세션 시작 시 1부터 다시 센다.
5. 중앙 수집기 `backend/.../SensorArchiveCollector.java`가 두 topic을 모두 구독한다. 문서에 `receivedAt`(**서버 시계**)과 `rawPayload`(수신 원문)를 붙여 Influx `sensor_original`에 쓴다(`InfluxTelemetryRepository.append`). Influx `_time`=observedAt+(sequence mod 1e6) ns이며 이 시각은 identity 보정값이다. 분석에는 문서의 `observedAt`을 쓴다.
   - qualified 행이 같은 observedAt/값으로 들어오면 legacy UNVERIFIED 행을 삭제한다. 그래서 **최신 1초 안쪽에서는 UNVERIFIED 행이 잠깐 보일 수 있다**(read-1의 temp_humidity, read-2의 8개 채널 head에서 실제로 관측됨). consumer는 `quality=="QUALIFIED"`로 거른다.
6. DeviceTwin/DeviceStatus는 reportCycle 5 s의 별도 경로이며, read-1에서 twin 시각이 최신 원본보다 약 20.6 s 늦었다. **Twin poll을 새 측정값으로 쓰면 안 된다.**

## 3. 신선도: 약 10초 간격 2회 읽기 (`reads/read-1.json`, `reads/read-2.json`)

| | read-1 | read-2 |
|---|---|---|
| capturedAt (ser0001) | 10:51:24.567Z | 10:51:35.256Z |
| 최신 QUALIFIED observedAt | 11개 모두 10:51:24.668Z | 10:51:34.672Z / 10:51:35.673Z |
| DeviceStatus state | 11개 모두 online | 11개 모두 online |
| collector status | CONNECTING, stored 47475 | CONNECTING, stored 47717 (+242) |

- Δsequence는 10 (환경 4개 채널)과 11 (나머지 7개)이었다. 두 읽기 사이에 채널별 API 조회가 차례로 실행되면서 생긴 경계 차이다.
- capturedAt보다 observedAt이 조금 뒤인 것은 서로 다른 호스트 시계(ser0001과 dev0003)를 비교했기 때문이다. 시계 오프셋은 측정하지 않았다.
- collector `CONNECTING`은 구독 broker 중 일부(dev0001 jetorn NanoMQ가 Pending/Terminating)가 연결되지 않은 상태로 보인다. dev0003 저장은 진행 중이었다(storedDeliveries 증가). 원인은 확정하지 않았다.

## 4. 원본 이력 15분 (`history/*.json`, `history/manifest.json`)

- 요청: `GET /api/v1/data-store/sensors/{uid}/{property}/history?from=2026-10-21T10:36:40Z&to=2026-10-21T10:51:40Z&limit=2000&order=asc`. cursor는 따라가지 않았다(필요 없었음). API 응답 바이트를 가공 없이 저장했고 보간이나 재표본화는 하지 않았다.
- 채널마다 900행, `truncated=false`, `quality` QUALIFIED 900/900, `timeBasis` SOURCE 900/900. 세션은 1개, sequence는 395→1294로 연속이다(끊김 0, 중복 0, observedAt 중복 0, 비단조 0).
- observedAt 간격 (ms): min 800, p50 1000, p99 1002, max 1005–1201. 1.5 s를 넘는 간격은 0개다. 800/1200은 5 Hz frame 경계에서 생긴 지터다.
- 직전 값과 같은 값이 연속으로 나온 횟수: temp_humidity 78, temp_pressure 110, gyro_z 68 등이다. 이는 같은 값이 다시 측정된 것이며 결측이 아니다(sequence가 증가함).
- 지연(참고만, E2E 아님): mapper publish − origin은 p50 114 ms, max ~210 ms이며 같은 dev0003 시계로 쟀다. 서버 receivedAt − origin은 p50 118 ms인데 **시계가 서로 달라 skew가 섞여 있다**. API 조회 시간(`request.fetchMs`)은 source E2E가 아니다.
- **교차 채널 정렬 키**: 같은 5 Hz frame에서 나온 값은 observedAt(ms)이 정확히 같다. 11개 채널이 공유하는 observedAt은 898/902개이고 채널마다 2개씩 다르다. 정렬은 observedAt **완전 일치**로만 하고 nearest/보간 정렬은 하지 않는다. sessionId는 채널마다 다르고 sequence는 이번 세션에서 우연히 같은 값일 뿐이므로 **채널 간 조인 키로 쓰지 않는다**. 채널 안에서의 순서·결측 판정 키는 (sourceGeneration, sessionId, sequence)다.
- 오래된 기록: gyro_x의 가장 오래된 보존 행은 2026-10-13T07:27:55Z이며 **UNVERIFIED** (sequence 없음, legacy)다(`reads/gyro_x-earliest-retained.json`). QUALIFIED 기록이 언제부터 시작되는지는 확인하지 않았다.
- 24시간 저장 건수(`reads/gyro_x-chart-24h.json`, 10분 bucket): 대부분 600/bucket (1 Hz)이다. **2026-10-21 06:05–07:11Z 근처는 저장되지 않았다**(0개 bucket 6개). 09:50Z bucket은 462개였다. 이번 세션은 mapper 컨테이너 재시작(10:30:06Z, restarts=4, lastState Error/exit 2)으로 시작됐다. 같은 날 10:27:41Z에 dev0003 터널이 끊긴 기록과 09:56Z NodeNotReady 이벤트도 있다(`docs/experiments/scheduling-baseline-smoke-20261021.md`). 재시작 원인은 확정하지 않았다. 과거 데이터를 재생할 구간을 고를 때는 세션 경계와 이 공백을 피해야 한다.

## 5. 라이브 수신 (`mqtt/live-25s.raw.txt`, `mqtt/command.txt`)

- `mosquitto_sub -h 10.254.164.138 -p 1883 -q 0 -W 25 -v -F '%U|%t|%p' -t 'edgeai/observations/v1/edgeai-devices/+/+' -t 'edgeai/devices/+/+'`를 실행했다. 시작 시각 10:50:53.600Z, 종료 코드 27은 `-W` 시간 초과로 정상이다.
- NanoMQ 설정(configmap `kubeedge/edgeai-nanomq-raspi5`)은 bind `0.0.0.0:1883`, `allow_anonymous=true`다. publish는 하지 않았고 QoS 0, clean session으로 구독만 했다.
- 수신 550줄 중 Sense HAT은 topic 22개(원본 11 + qualified 11)이고 각각 25 msg를 받았다. qualified topic마다 sequence 1248→1272가 연속이었고 세션은 history의 세션과 같았다.
- 각 줄의 `%U`는 ser0001이 수신한 시각이다. dev0003 시계로 찍힌 observedAt/receivedAt과 비교하면 skew가 섞인다.

## 6. 반복 가능한 안전한 접근 방법

- 이력: ser0001(control-plane 호스트)에서 API ClusterIP `http://10.104.123.82:18080`에 GET 요청을 보낸다(인증 없이 200). 위 §4의 history API를 쓰며 `limit≤2000`, 초과분은 `nextCursor`로 넘긴다. 기간은 최대 31일, 시작 포함·끝 미포함이다. ClusterIP는 Service를 재생성하면 바뀌므로 `kubectl -n edgeai get svc edgeai-api`로 다시 확인한다. 대시보드 13080의 이 경로는 501을 반환했다. env/Pod spec 전체를 덤프하지 말고 `tools/capture.py`처럼 필드만 projection한다.
- 재현 명령 (출력 디렉터리는 새 경로를 지정해야 한다. 같은 경로에 쓰면 덮어쓰기를 거부한다):
  ```
  rtk proxy python3 -I artifacts/sensehat-placement/discovery-20261021/tools/capture.py <NEW_DIR> point read-1
  rtk proxy python3 -I artifacts/sensehat-placement/discovery-20261021/tools/capture.py <NEW_DIR> history
  rtk proxy python3 -I artifacts/sensehat-placement/discovery-20261021/tools/analyze.py <NEW_DIR>   # reads/read-{1,2}, mqtt 파일 필요
  ```
- 라이브: §5의 mosquitto_sub에 `-W`를 지정해 실행 시간을 제한한다. 단일 채널은 `-t edgeai/observations/v1/edgeai-devices/sensehat-001-humidity/humidity`처럼 topic을 지정한다.

## 7. dev0003에서 consumer를 실행할 수 있는가 (배포하지 않음)

- 근거: broker가 hostNetwork NanoMQ이고 0.0.0.0:1883, 익명 접속을 허용한다. consumer는 hostNetwork일 때 `127.0.0.1:1883`, 아닐 때 노드 IP `10.254.164.138:1883`으로 구독하면 된다. 이 방식은 mapper Pod·Device CR·broker 설정을 바꾸지 않는다. 노드 arm64, CPU 4 / 메모리 8 GiB이고 requests는 cpu 785m(19%), mem 396Mi(4%)이며 taint는 없다. 기존 scheduler 실험에서도 dev0003에 worker Pod를 배치한 적이 있다(`docs/experiments/scheduling-baseline-smoke-20261021.md`).
- 확인하지 않은 것: Pod 네트워크(flannel-edge/edgemesh)에서 노드 IP:1883으로 연결되는지, 구독자가 추가될 때 mapper publish(QoS1, 2 s timeout)에 영향이 있는지. **Pod 배포는 이번 범위가 아니어서 하지 않았다.**
- 위험: mapper 재시작이나 dev0003 터널 단절이 일어나면 세션이 바뀌고 sequence가 1부터 다시 시작한다. 장애 구간은 결측으로 기록하고 메우지 않는다.

## 8. 작은 센서 window 추론에 쓸 수 있는 기존 자산 (조사만)

- scheduler 저장소의 ONNX(`artifacts/e0-accelerators/*.onnx`, `e1-dag/additional-assets/model.onnx` 등)는 SqueezeNet 이미지 모델이다. ResNet18/Wav2Vec2/MUTAN 경로도 영상·음성용이다. **Sense HAT 시계열용으로 학습된 모델은 없다.**
- 쓸 수 있는 실행환경 후보: e0의 onnxruntime 1.23.2 CPU 경로(Python), edgeai 중앙 eKuiper 연속 VD(규칙/수식 계산이며 학습 모델이 아님). edgeai Runner는 amd64 개발 이미지라 ARM64 수용을 확인하지 않았다.
- coordinator가 구상한 선형 예측 기준선은 새로 학습해야 한다. 학습·구현은 이번 작업에서 하지 않았다.

## 9. 한계와 남은 작업

- 시계 오프셋(dev0003↔ser0001)을 측정하지 않았다. 교차 호스트 지연 수치는 해석하지 않는다.
- 5 Hz source frame은 1 Hz로 간추려진 값만 볼 수 있다. 더 높은 속도가 필요하면 mapper를 변경해야 하며 이번 범위를 벗어난다.
- QUALIFIED 이력이 시작된 시점, 이전 세션들의 gap 분포, collector `CONNECTING`의 원인은 조사하지 않았다.
- dev0003 Pod에서 실제로 구독되는지는 다음 단계에서 Pod 배포 승인을 받은 뒤 검증해야 한다.

## 실행 기록

- 클러스터 변경 없음. GET/`kubectl get`(필드 projection)/MQTT subscribe만 수행했다. 쓰기·publish·배포·commit은 하지 않았다.
- 1차 시도 point read는 base64 padding 오류로 파일 없이 실패했고, 수정 후 다시 실행한 결과만 보존했다. MQTT 캡처는 그 1차 시도와 함께(10:50:53Z) 실행한 것을 보존했다.
- 모든 셸 명령은 `rtk proxy`로 실행했다.
