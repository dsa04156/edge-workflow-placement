"""Qualify the same ONNX classifier on an allocated GPU or Intel NPU.

CUDA uses a recorded ONNX-to-PyTorch conversion because ORT 1.23.2 has no
official ARM64 GPU wheel. Quality comparisons use local ORT CPU reference.
"""

import hashlib
import importlib.metadata as metadata
import json
import os
from pathlib import Path
import platform
import re
import time

from probe import MODEL_URL, MODEL_SHA256, IMAGE_URL, IMAGE_SHA256, fetch, emit
from transport import preprocess, TENSOR_SHA256


def gpu_backend(model_path, tensor):
    import numpy as np
    import onnx
    from onnx import numpy_helper, version_converter
    import torch
    from onnx2torch import convert

    assert torch.cuda.is_available(), "CUDA unavailable; CPU execution forbidden"
    torch.backends.cuda.matmul.allow_tf32 = False
    torch.backends.cudnn.allow_tf32 = False
    torch.set_num_threads(1)
    started = time.perf_counter()
    original = onnx.load(str(model_path))
    normalized = version_converter.convert_version(original, 13)
    onnx.checker.check_model(normalized)
    weights = {v.name: numpy_helper.to_array(v) for v in normalized.graph.initializer}
    for value in original.graph.initializer:
        before, after = numpy_helper.to_array(value), weights[value.name]
        assert before.dtype == after.dtype and before.shape == after.shape
        assert np.array_equal(before, after), value.name
    derived_path = model_path.with_name("squeezenet1.1-derived-opset13.onnx")
    onnx.save(normalized, str(derived_path))
    derived_sha256 = hashlib.sha256(derived_path.read_bytes()).hexdigest()
    assert derived_sha256 == "5846b40aca708f9e817e19caa3d49e49e81b9bd9ba2fffc70a175a8558904857"
    emit("gpu_graph_normalized", source_sha256=MODEL_SHA256, derived_sha256=derived_sha256,
         target_opset=13, original_initializers_unchanged=len(original.graph.initializer),
         normalization_ms=(time.perf_counter()-started)*1000)
    model = convert(normalized).eval().to("cuda")
    torch.cuda.synchronize()
    parameters = list(model.parameters())
    assert parameters and all(p.is_cuda for p in parameters)
    emit("prepared", backend="onnx2torch_cuda", conversion_and_device_load_ms=(time.perf_counter()-started)*1000,
         device_name=torch.cuda.get_device_name(0), capability=list(torch.cuda.get_device_capability(0)),
         cuda_build=torch.version.cuda, all_model_parameters_cuda=True,
         parameter_elements=sum(p.numel() for p in parameters))

    def infer(input_tensor=None):
        with torch.inference_mode():
            copied = time.perf_counter()
            inputs = torch.from_numpy(tensor if input_tensor is None else input_tensor).to("cuda")
            torch.cuda.synchronize()
            h2d_ms = (time.perf_counter() - copied) * 1000
            start, end = torch.cuda.Event(enable_timing=True), torch.cuda.Event(enable_timing=True)
            start.record()
            output = model(inputs)
            end.record()
            torch.cuda.synchronize()
            assert output.is_cuda
            cuda_ms = start.elapsed_time(end)
            assert cuda_ms > 0
            copied = time.perf_counter()
            scores = output.detach().cpu().numpy().reshape(-1).copy()
            d2h_ms = (time.perf_counter() - copied) * 1000
            return scores, {"cuda_event_ms": cuda_ms, "output_device": str(output.device),
                            "h2d_blocking_wall_ms": h2d_ms, "d2h_blocking_wall_ms": d2h_ms}

    try:
        with torch.profiler.profile(activities=[torch.profiler.ProfilerActivity.CPU,
                                               torch.profiler.ProfilerActivity.CUDA]) as profiler:
            infer()
        events = [e for e in profiler.events() if "cuda" in str(e.device_type).lower()]
        emit("gpu_profile", cuda_event_count=len(events), kernel_names=[e.name for e in events[:8]])
    except Exception as error:
        emit("gpu_profile_unavailable", error=str(error)[:400])
    return infer


def npu_backend(model_path, tensor):
    import openvino as ov

    core = ov.Core()
    assert any(re.fullmatch(r"NPU(?:\.\d+)?", d) for d in core.available_devices)
    emit("npu_available", version=ov.__version__, devices=core.available_devices,
         device_name=str(core.get_property("NPU", "FULL_DEVICE_NAME")))
    started = time.perf_counter()
    model = core.read_model(str(model_path))
    read_ms = (time.perf_counter() - started) * 1000
    cache = Path("/work/ov-cache")
    cache.mkdir()
    core.set_property({"CACHE_DIR": str(cache)})
    started = time.perf_counter()
    compiled = core.compile_model(model, "NPU", {"PERFORMANCE_HINT": "LATENCY"})
    compile_ms = (time.perf_counter() - started) * 1000
    devices = compiled.get_property("EXECUTION_DEVICES")
    if isinstance(devices, str):
        devices = [devices]
    assert devices and all(re.fullmatch(r"NPU(?:\.\d+)?", d) for d in devices), devices
    request = compiled.create_infer_request()
    emit("prepared", backend="openvino_direct_npu", model_read_ms=read_ms,
         compile_ms=compile_ms, execution_devices=list(devices),
         precision="FP32 model and I/O; NPU internal precision is device-dependent",
         cache_bytes=sum(p.stat().st_size for p in cache.rglob("*") if p.is_file()))

    def infer(input_tensor=None):
        request.infer({compiled.input(0): tensor if input_tensor is None else input_tensor})
        return request.get_output_tensor(0).data.reshape(-1).copy(), {"execution_devices": list(devices)}

    return infer


def main():
    import numpy as np
    import onnxruntime as ort

    backend = os.environ["BACKEND"]
    emit("environment", node=os.environ["NODE_NAME"], backend=backend,
         python=platform.python_version(), architecture=platform.machine())
    packages = {}
    for name in ["numpy", "Pillow", "onnxruntime", "torch", "torchvision", "onnx", "onnx2torch", "openvino"]:
        try:
            packages[name] = metadata.version(name)
        except metadata.PackageNotFoundError:
            pass
    emit("packages", versions=packages)
    model_path = Path("/work/squeezenet1.1-7.onnx")
    model_path.write_bytes(fetch(MODEL_URL, MODEL_SHA256))
    tensor = preprocess(fetch(IMAGE_URL, IMAGE_SHA256))
    digest = hashlib.sha256(tensor.tobytes()).hexdigest()
    assert digest == TENSOR_SHA256
    opts = ort.SessionOptions()
    opts.intra_op_num_threads = opts.inter_op_num_threads = 1
    reference_session = ort.InferenceSession(str(model_path), opts, providers=["CPUExecutionProvider"])
    reference = reference_session.run(None, {reference_session.get_inputs()[0].name: tensor})[0].reshape(-1)
    expected_top5 = np.argsort(reference)[-5:][::-1].tolist()
    assert expected_top5 == [258, 259, 157, 279, 152]
    emit("cpu_reference", providers=reference_session.get_providers(), model_sha256=MODEL_SHA256,
         input_sha256=digest, top5=expected_top5, output_bytes=reference.nbytes)
    assert backend in {"gpu", "intel_npu"}, backend
    infer = gpu_backend(model_path, tensor) if backend == "gpu" else npu_backend(model_path, tensor)
    for iteration in range(7):
        started = time.perf_counter()
        scores, evidence = infer()
        elapsed_ms = (time.perf_counter() - started) * 1000
        assert scores.shape == (1000,) and np.isfinite(scores).all()
        top5 = np.argsort(scores)[-5:][::-1].tolist()
        assert top5 == expected_top5, (top5, expected_top5)
        difference = np.asarray(scores, dtype=np.float64) - reference
        emit("inference", iteration=iteration, warmup=iteration < 2,
             blocking_host_input_to_host_output_ms=elapsed_ms, top5=top5,
             max_abs_error_vs_cpu=float(np.max(np.abs(difference))),
             rmse_vs_cpu=float(np.sqrt(np.mean(difference**2))), **evidence)
    emit("complete", backend=backend, status="classification_fixture_passed",
         warmup=2, measured=5, model_sha256=MODEL_SHA256, input_sha256=digest,
         scope="single_fixture_accelerator_qualification_not_policy_benchmark")


if __name__ == "__main__":
    main()
