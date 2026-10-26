# Input-contract erratum (2026-10-26, written before evaluation execution)

- **Earlier assumption (wrong):** "The MXQ fuses normalization, so it takes uint8 HWC input." This was taken from the `mblt-sdk-tutorial` runtime tutorial. It appears only as the code comment on `MEAN, STD` in `experiments/mobilint-dag-pilot/protocol.py` (`# applied only for the FP32 ONNX arm (MXQ fuses normalization)`). That file is part of the run-bound code (release hashes), so the comment was not rewritten. `frozen-protocol.json` stores only the mean/std values and does not contain this sentence. (Erratum corrected 2026-10-26: the first version wrongly said the frozen JSON contained the sentence.) The tutorial describes models compiled with that tutorial. It does not describe this model-zoo MXQ.
- **Observed:**
  - Probe 1 `mdp-20261026t091406`: qbruntime rejected a read-only buffer. This was a code defect, now fixed with a copy.
  - Probe 2 `mdp-20261026t091641`: qbruntime 1.4.0 returned `Model_DtypeMismatched`. The MXQ metadata in health says `input_dtype=DataType.Float32`.
  - Neither probe produced a Mobilint output. Both probes used warmup[0] (`w2-…`), which is disjoint from the 200-sample evaluation set, so no evaluation output was observed.
- **Official contract (vendor source, hashes in `provenance/provenance.json`; third-party source files are not republished):** `mblt-vision-python` 0.0.6, `mblt_vision/models/ResNet50.yaml`, `IMAGENET1K_V1` (`file_cfg`: `mobilint/ResNet50.tv1_in1k` / `resnet50_IMAGENET1K_V1.mxq`). The pipeline is: Reader pil → Resize 256 bilinear → CenterCrop 224 → SetOrder HWC → Normalize torch. The `normalize.py` math is float32 /255, then float64 mean/std, then a cast to float32. Wheel sha256 `60562a5c…`, BSD-3-Clause.
- **Change:**
  - `worker.MobilintBackend.infer` now feeds `normalize_hwc_vendor(arr)`: float32 normalized HWC, exactly the vendor math. The child process copies the buffer (it must be writeable).
  - The edge preprocessing already equals the vendor PIL path: same resize size formula, PIL bilinear, `round()` crop offsets.
  - The wire tensor is still the identical uint8 HWC (150,528 B) for both arms.
  - The CPU arm is unchanged: ORT FP32, normalize_nchw. It agrees with the vendor HWC normalization within 1e-6 after transpose.
- **Unchanged:** frozen selection/order (`frozen-protocol.json` sha `ecb4f920…`), assets, images, deadlines, resources and claims. This erratum supersedes the `protocol.py` comment. Neither the comment nor the frozen file is rewritten.
- **Release:** `release-normalized.json` (worker.py `46e51e60…`), bound by the coordinator after checking the diff and running 3 targeted tests.
