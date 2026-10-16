"""Application model lifecycle; deliberately does not flush driver/OS caches."""

import gc
import threading
import time


class ModelRuntime:
    def __init__(self, factory, fixture):
        self.factory=factory
        self.fixture=fixture
        self.infer=None
        self.generation=0
        self.lock=threading.Lock()

    def health(self):
        with self.lock:
            return {'model_ready':self.infer is not None,'generation':self.generation}

    def evict(self):
        started=time.perf_counter()
        with self.lock:
            self.infer=None
            gc.collect()
            return {'model_ready':False,'generation':self.generation,
                    'eviction_ms':(time.perf_counter()-started)*1000}

    def run(self,tensor):
        import numpy as np
        with self.lock:
            cold=self.infer is None
            preparation_ms=0.0
            details={}
            if cold:
                started=time.perf_counter()
                infer,details=self.factory()
                warmed=time.perf_counter()
                for _ in range(2):
                    scores,_=infer(self.fixture)
                    assert np.isfinite(scores).all()
                    assert np.argsort(scores)[-5:][::-1].tolist()==[258,259,157,279,152]
                self.infer=infer
                self.generation+=1
                details['warmup_ms']=(time.perf_counter()-warmed)*1000
                preparation_ms=(time.perf_counter()-started)*1000
            scores,device=self.infer(tensor)
            return scores,{**device,'was_cold':cold,'generation':self.generation,
                           'preparation_ms':preparation_ms,'preparation_details':details}


def factory_for(backend,model_path,options):
    if backend=='cpu':
        import onnxruntime as ort
        def factory():
            started=time.perf_counter()
            session=ort.InferenceSession(str(model_path),options,providers=['CPUExecutionProvider'])
            name=session.get_inputs()[0].name
            def infer(tensor):
                return session.run(None,{name:tensor})[0].reshape(-1),{'execution_device':'CPU'}
            return infer,{'session_load_ms':(time.perf_counter()-started)*1000}
        return factory
    assert backend=='intel_npu','Dynamic pilot supports CPU and Intel NPU only'
    import openvino as ov
    core=ov.Core()
    core.set_property({'CACHE_DIR':''})
    def factory():
        started=time.perf_counter()
        model=core.read_model(str(model_path))
        read_ms=(time.perf_counter()-started)*1000
        started=time.perf_counter()
        compiled=core.compile_model(model,'NPU',{'PERFORMANCE_HINT':'LATENCY'})
        compile_ms=(time.perf_counter()-started)*1000
        devices=compiled.get_property('EXECUTION_DEVICES')
        if isinstance(devices,str):devices=[devices]
        assert devices and all(d=='NPU' or d.startswith('NPU.') for d in devices)
        started=time.perf_counter()
        request=compiled.create_infer_request()
        request_ms=(time.perf_counter()-started)*1000
        def infer(tensor):
            request.infer({compiled.input(0):tensor})
            return request.get_output_tensor(0).data.reshape(-1).copy(),{'execution_devices':list(devices)}
        return infer,{'read_ms':read_ms,'compile_ms':compile_ms,'create_request_ms':request_ms}
    return factory
