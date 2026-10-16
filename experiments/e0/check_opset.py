"""Check derived ONNX opset normalization against the original CPU fixture."""

import hashlib
import json
from pathlib import Path
import platform
import numpy as np
import onnx
from onnx import numpy_helper, version_converter
import onnxruntime as ort

from probe import MODEL_URL, MODEL_SHA256, IMAGE_URL, IMAGE_SHA256, fetch
from transport import preprocess, TENSOR_SHA256


base = Path("artifacts/e0-accelerators")
original_path = base / "original-model.onnx"
original_path.write_bytes(fetch(MODEL_URL, MODEL_SHA256))
original = onnx.load(str(original_path))
normalized = version_converter.convert_version(original, 13)
onnx.checker.check_model(normalized)
original_weights = {v.name: numpy_helper.to_array(v) for v in original.graph.initializer}
normalized_weights = {v.name: numpy_helper.to_array(v) for v in normalized.graph.initializer}
assert original_weights and all(name in normalized_weights
                                and value.dtype == normalized_weights[name].dtype
                                and value.shape == normalized_weights[name].shape
                                and np.array_equal(value, normalized_weights[name])
                                for name, value in original_weights.items())
path = base / "derived-opset13.onnx"
onnx.save(normalized, str(path))
tensor = preprocess(fetch(IMAGE_URL, IMAGE_SHA256))
input_sha256 = hashlib.sha256(tensor.tobytes()).hexdigest()
assert input_sha256 == TENSOR_SHA256
options = ort.SessionOptions()
options.intra_op_num_threads = options.inter_op_num_threads = 1
outputs = []
for model in [original_path, path]:
    session = ort.InferenceSession(str(model), options, providers=["CPUExecutionProvider"])
    outputs.append(session.run(None, {session.get_inputs()[0].name: tensor})[0].reshape(-1))
assert np.array_equal(outputs[0], outputs[1]), np.max(np.abs(outputs[0] - outputs[1]))
result = {"original_sha256": MODEL_SHA256,
          "input_sha256": input_sha256,
          "top5": np.argsort(outputs[0])[-5:][::-1].tolist(),
          "environment": {"python": platform.python_version(), "onnx": onnx.__version__,
                          "onnxruntime": ort.__version__, "numpy": np.__version__},
          "derived_sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
          "original_opsets": [{"domain": x.domain, "version": x.version} for x in original.opset_import],
          "derived_opsets": [{"domain": x.domain, "version": x.version} for x in normalized.opset_import],
          "original_initializers_unchanged": len(original_weights),
          "cpu_fixture_output_bitwise_equal": True,
          "max_abs_difference": float(np.max(np.abs(outputs[0] - outputs[1]))),
          "scope": "one_fixture_not_general_semantic_equivalence_proof"}
(base / "opset-check.json").write_text(json.dumps(result, indent=2) + "\n")
print(json.dumps(result), flush=True)
