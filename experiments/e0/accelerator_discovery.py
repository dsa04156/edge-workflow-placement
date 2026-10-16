"""Inspect an allocated accelerator and the existing image's runtime stack."""

import importlib.metadata as metadata
import importlib.util
import json
import os
from pathlib import Path
import platform
import subprocess


result = {"node": os.environ["NODE_NAME"], "backend": os.environ["BACKEND"],
          "python": platform.python_version(), "architecture": platform.machine(),
          "packages": {}, "modules": {}, "devices": {}}
for package in ["torch", "torchvision", "onnx", "onnxruntime", "onnxruntime-gpu",
                "openvino", "tensorrt", "onnx2torch", "numpy", "Pillow"]:
    try:
        result["packages"][package] = metadata.version(package)
    except metadata.PackageNotFoundError:
        pass
for module in ["torch", "torchvision", "onnx", "onnxruntime", "openvino",
               "tensorrt", "onnx2torch", "numpy", "PIL"]:
    result["modules"][module] = importlib.util.find_spec(module) is not None
result["devices"] = {"nvidia": len(list(Path("/dev").glob("nvidia[0-9]*"))),
                     "accel": len(list(Path("/dev/accel").glob("accel*")))}
if os.environ["BACKEND"] == "gpu":
    try:
        import torch
        result["torch"] = {"cuda_build": torch.version.cuda,
                           "cuda_available": torch.cuda.is_available()}
        if torch.cuda.is_available():
            result["torch"].update(device_name=torch.cuda.get_device_name(0),
                                   capability=list(torch.cuda.get_device_capability(0)))
    except Exception as error:
        result["torch_error"] = str(error)
    try:
        import onnxruntime as ort
        result["ort"] = {"version": ort.__version__, "providers": ort.get_available_providers()}
    except ImportError:
        pass
    try:
        p = subprocess.run(["nvidia-smi", "--query-gpu=name,driver_version,memory.total",
                            "--format=csv,noheader"], capture_output=True, text=True, timeout=15)
        result["nvidia_smi"] = {"exit_code": p.returncode, "stdout": p.stdout.strip()}
    except (OSError, subprocess.TimeoutExpired):
        result["nvidia_smi"] = {"available": False}
elif os.environ["BACKEND"] == "intel_npu":
    try:
        import openvino as ov
        core = ov.Core()
        result["openvino"] = {"version": ov.__version__, "available_devices": core.available_devices}
        for prop in ["FULL_DEVICE_NAME", "NPU_DRIVER_VERSION", "NPU_COMPILER_VERSION"]:
            try:
                result["openvino"][prop] = str(core.get_property("NPU", prop))
            except Exception as error:
                result["openvino"][prop] = {"unavailable": str(error)[:300]}
    except Exception as error:
        result["openvino_error"] = str(error)
print(json.dumps(result), flush=True)
