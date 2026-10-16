# E3 공개 이미지 corpus 선정 메모

확인일: 2026-10-16. Context7은 월간 quota 초과로 사용할 수 없었다. GitHub MCP의 공식 저장소 파일·metadata와 원저작자 자료를 확인했다. 초기 조사 뒤 URL 오류를 수정하면서 7개 이미지의 실제 HTTP 다운로드 bytes·SHA256을 검증했다. 모델은 실행하지 않았으며 다른 파일·클러스터는 변경하지 않았다.

## 실행 전에 고정할 7개 fixture

PyTorch/vision gallery의 이미지별 권리 안내를 이번 조사에서 확보하지 못해, 개별 이미지 귀속·권리 조건을 명시한 공식 scikit-image assets를 선택했다. release `v0.25.2`가 가리키는 commit **e8a42ba85aaf5fd9322ef9ca51bc21063b22fcae**로 고정했다. 아래 다운로드는 모두 해당 commit의 실제 파일이다. bytes는 실제 다운로드 길이와 GitHub API metadata가 일치함을 확인했으며, 해상도·색상은 [공식 asset 함수](https://github.com/scikit-image/scikit-image/blob/e8a42ba85aaf5fd9322ef9ca51bc21063b22fcae/skimage/data/_fetchers.py) 기준이며 실행 전 실제 파일과 다시 대조한다.

| 고정 다운로드 URL / filename | 가로×세로·색상 | bytes | 귀속·권리 조건 |
|---|---|---:|---|
| [astronaut.png](https://raw.githubusercontent.com/scikit-image/scikit-image/e8a42ba85aaf5fd9322ef9ca51bc21063b22fcae/skimage/data/astronaut.png) | 512×512 RGB | 791555 | NASA Eileen Collins; public domain ([출처](https://github.com/scikit-image/scikit-image/blob/e8a42ba85aaf5fd9322ef9ca51bc21063b22fcae/skimage/data/_fetchers.py#:~:text=def%20astronaut)) |
| [camera.png](https://raw.githubusercontent.com/scikit-image/scikit-image/e8a42ba85aaf5fd9322ef9ca51bc21063b22fcae/skimage/data/camera.png) | 512×512 gray | 139512 | Lav Varshney; CC0 ([출처](https://github.com/scikit-image/scikit-image/blob/e8a42ba85aaf5fd9322ef9ca51bc21063b22fcae/skimage/data/_fetchers.py#:~:text=def%20camera)) |
| [chelsea.png](https://raw.githubusercontent.com/scikit-image/scikit-image/e8a42ba85aaf5fd9322ef9ca51bc21063b22fcae/skimage/data/chelsea.png) | 451×300 RGB | 240512 | Stefan van der Walt; CC0 ([출처](https://github.com/scikit-image/scikit-image/blob/e8a42ba85aaf5fd9322ef9ca51bc21063b22fcae/skimage/data/_fetchers.py#:~:text=def%20chelsea)) |
| [coffee.png](https://raw.githubusercontent.com/scikit-image/scikit-image/e8a42ba85aaf5fd9322ef9ca51bc21063b22fcae/skimage/data/coffee.png) | 600×400 RGB | 466706 | Rachel Michetti; CC0; Pikolo Espresso Bar 제공 ([출처](https://github.com/scikit-image/scikit-image/blob/e8a42ba85aaf5fd9322ef9ca51bc21063b22fcae/skimage/data/_fetchers.py#:~:text=def%20coffee)) |
| [hubble_deep_field.jpg](https://raw.githubusercontent.com/scikit-image/scikit-image/e8a42ba85aaf5fd9322ef9ca51bc21063b22fcae/skimage/data/hubble_deep_field.jpg) | 1000×872 RGB | 527940 | NASA/Hubble; public domain ([출처](https://github.com/scikit-image/scikit-image/blob/e8a42ba85aaf5fd9322ef9ca51bc21063b22fcae/skimage/data/_fetchers.py#:~:text=def%20hubble_deep_field)) |
| [retina.jpg](https://raw.githubusercontent.com/scikit-image/scikit-image/e8a42ba85aaf5fd9322ef9ca51bc21063b22fcae/skimage/data/retina.jpg) | 1411×1411 RGB | 269564 | Mikael Häggström; CC0 1.0 ([출처](https://github.com/scikit-image/scikit-image/blob/e8a42ba85aaf5fd9322ef9ca51bc21063b22fcae/skimage/data/_fetchers.py#:~:text=def%20retina)) |
| [rocket.jpg](https://raw.githubusercontent.com/scikit-image/scikit-image/e8a42ba85aaf5fd9322ef9ca51bc21063b22fcae/skimage/data/rocket.jpg) | 640×427 RGB | 112525 | SpaceX DSCOVR 발사; 공식 source가 public domain으로 명시 ([출처](https://github.com/scikit-image/scikit-image/blob/e8a42ba85aaf5fd9322ef9ca51bc21063b22fcae/skimage/data/_fetchers.py#:~:text=def%20rocket)) |

모든 후보는 사전 상한 **2,000,000 bytes** 이하다. 이 목록은 모델 출력 전에 고정한다. `ihc.png`는 공식 설명이 “No known copyright restrictions”에 머물러, 명명된 라이선스가 있는 후보를 우선하는 선정 기준에 따라 제외했다. 내용·크기·형식 다양성은 권리 조건을 만족한 이 7개 파일에서 얻으며 추가 가공으로 차이를 만들지 않는다.

## URL 오류 원인과 수정

초기 메모의 `23308914c880574fe7c716e2ce9aa698ab0d69b4`는 **commit이 아니라 annotated tag object SHA**였다. GitHub MCP가 `scikit-image/scikit-image`의 `v0.25.2` contents metadata에 표시한 SHA를 commit으로 오인해 raw URL을 조립했고, 실제 다운로드를 확인하지 않은 채 유효 URL처럼 안내했다. 첫 astronaut URL의 HTTP 404를 직접 재현했다. 다른 데이터 저장소의 commit을 혼합한 문제는 아니었다.

공식 [tag ref API](https://api.github.com/repos/scikit-image/scikit-image/git/ref/tags/v0.25.2)에서 type=`tag`를 확인한 뒤 [tag object API](https://api.github.com/repos/scikit-image/scikit-image/git/tags/23308914c880574fe7c716e2ce9aa698ab0d69b4)를 한 번 더 역참조하여 type=`commit`, SHA=`e8a42ba85aaf5fd9322ef9ca51bc21063b22fcae`를 얻었다. 모든 raw/source 링크를 이 실제 commit으로 수정했다. 기존 filename 7개·bytes·기대 SHA256은 유지되어 **입력 corpus bytes는 변경되지 않았다**.

## 파일 검증

아래 SHA256은 같은 commit의 [공식 registry](https://github.com/scikit-image/scikit-image/blob/e8a42ba85aaf5fd9322ef9ca51bc21063b22fcae/skimage/data/_registry.py)에 기록된 기대값이며, 수정한 commit raw URL 7개를 실제로 다운로드해 `hashlib.sha256`으로 계산한 값과 **전부 일치**했다. 7개 모두 **HTTP 200**, 위 표의 bytes와 일치하고 2,000,000 bytes 이하였다. 이미지 decode·모델 출력은 이번 검증에서 확인하지 않았다. 실행 담당자는 자신의 다운로드에서도 동일 검사를 수행한다.

| filename | 기대 SHA256 |
|---|---|
| astronaut.png | `88431cd9653ccd539741b555fb0a46b61558b301d4110412b5bc28b5e3ea6cb5` |
| camera.png | `b0793d2adda0fa6ae899c03989482bff9a42d3d5690fc7e3648f2795d730c23a` |
| chelsea.png | `596aa1e7cb875eb79f437e310381d26b338a81c2da23439704a73c4651e8c4bb` |
| coffee.png | `cc02f8ca188b167c775a7101b5d767d1e71792cf762c33d6fa15a4599b5a8de7` |
| hubble_deep_field.jpg | `3a19c5dd8a927a9334bb1229a6d63711b1c0c767fb27e2286e7c84a3e2c2f5f4` |
| retina.jpg | `38a07f36f27f095e818aea7b96d34202c05176d30253c66733f2e00379e9e0e6` |
| rocket.jpg | `c2dd0de7c538df8d111e479619b129464d0269d0ae5fd18ca91d33a7fdfea95c` |

## 권리와 해석의 경계

- NASA·SpaceX public-domain 표기는 scikit-image 공식 asset 설명을 따른다. SpaceX 원 Flickr 페이지는 이번 web 도구에서 접근하지 못했다. retina는 원저작자의 [Wikimedia Own work·CC0 선언](https://commons.wikimedia.org/wiki/File:Fundus_photograph_of_normal_left_eye.jpg#Licensing)까지 확인했다. 파일과 함께 위 귀속·source URL을 보존하며 저장소 코드 라이선스를 모든 사진에 일괄 적용하지 않는다.
- 여기서 “원본”은 **위 commit에 배포된 asset bytes 그대로**라는 뜻이다. 카메라 촬영 master 원본·무편집 사진이라는 보증은 아니다. 재인코딩·추가 resize·synthetic payload padding을 하지 않고, RGB 변환과 Resize256/CenterCrop224 등 기존 SqueezeNet 전처리만 동일하게 적용한다.
- 정답 라벨이 부착된 평가 데이터셋이 아닌 작은 재현성 fixture 집합이다. ImageNet 대표성, 분류 accuracy, 일반 workload 성능 우월성을 주장하지 않는다. CPU reference의 top1 일치율, top5 set 일치·순서 일치·overlap을 구별하고 전체 출력 오차를 함께 기록한다. CPU 예측을 ground truth로 부르지 않는다.
- 기존 dog fixture는 bridge control로 유지하여 총 8개를 실행한다. E2에서 dog로 동결한 source별 배치가 새 입력에도 유지되는지 all-ARM/all-NPU-server 두 anchor를 paired 비교한다. worker/client Pod 전체를 3회 재배포하고 동일 corpus/model/input hash·backend·preprocess 계약을 유지한다. 실패·불일치·동률을 출력 확인 뒤 제외하지 않는다. 8배치 global optimum이나 새 알고리즘의 우월성을 주장하지 않으며, 이 메모에는 아직 새 Pod 실행·일치 결과가 없다.

