"""Pinned JellyBean model graph, CPU/batch1, trained weights loaded strictly.

Two compatibility changes: torchvision Scale -> Resize alias; skipthought
pretraining initialization omitted because every parameter is replaced strictly
by the published trained MUTAN state_dict. No model layer/weight is omitted.
"""
import ast
import hashlib
import io
import json
import os
from pathlib import Path
import re
import sys
import time


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def load(task):
    import numpy as np
    import torch
    root = Path(os.environ['MODEL_ROOT'])
    source = Path(os.environ['JELLYBEAN_SOURCE'])
    started = time.perf_counter()
    torch.set_num_threads(1)
    try:
        torch.set_num_interop_threads(1)
    except RuntimeError:
        pass  # Reference harness loads multiple stages sequentially in one process.
    sys.path.insert(0, str(source))
    metadata = {'scope': 'real_ai_file_replay', 'device': 'cpu', 'precision': 'float32',
                'torch': torch.__version__, 'threads': torch.get_num_threads(),
                'adapter_sha256': sha(__file__), 'source_commit': '65a1d67817946a4ae67b068eafb49d649bc061bc'}
    if task == 'vision':
        from PIL import Image
        import torchvision.models as models
        import torchvision.transforms as transforms
        weight = root / 'resnet18-f37072fd.pth'
        model = models.resnet18(weights=None)
        model.load_state_dict(torch.load(weight, weights_only=True, map_location='cpu'), strict=True)
        model = torch.nn.Sequential(*list(model.children())[:-2]).eval()
        transform = transforms.Compose([transforms.Resize(448), transforms.CenterCrop(448),
                                        transforms.ToTensor(), transforms.Normalize([.485, .456, .406], [.229, .224, .225])])

        def operator(data):
            image = Image.open(io.BytesIO(data)).convert('RGB')
            value = transform(image).unsqueeze(0)
            with torch.inference_mode():
                feature = model(value)
                feature = feature.sum(3).sum(2).div(feature.size(2) * feature.size(3)).squeeze(0).numpy()
            assert feature.shape == (512,) and feature.dtype == np.float32
            out = io.BytesIO()
            np.save(out, feature, allow_pickle=False)
            return out.getvalue()
        metadata.update(model_sha256=sha(weight), model='resnet18-imagenet1k-v1-noatt-448')
    elif task == 'speech':
        import soundfile as sf
        from transformers import Wav2Vec2ForCTC, Wav2Vec2Processor
        processor = Wav2Vec2Processor.from_pretrained(root / 'asr', local_files_only=True)
        model = Wav2Vec2ForCTC.from_pretrained(root / 'asr', local_files_only=True).eval()

        def operator(data):
            audio, rate = sf.read(io.BytesIO(data), dtype='float32')
            if rate != 16000 or audio.ndim != 1:
                raise ValueError('Expected mono 16kHz fixture')
            inputs = processor(audio, sampling_rate=16000, return_tensors='pt', padding='longest')
            with torch.inference_mode():
                logits = model(inputs.input_values).logits.squeeze(0)
                text = processor.decode(torch.argmax(logits, dim=-1), skip_special_tokens=True)
            return text.encode()
        metadata.update(model_sha256=sha(root / 'asr/model.safetensors'), model='wav2vec2-base-960h')
    elif task == 'fusion':
        import yaml
        import vqa.models as models
        import skipthoughts
        from gru import BayesianGRU
        vocab_path, weight = root / 'vocab.json', root / 'mutan-resnet18.pth.tar'
        vocab = json.loads(vocab_path.read_text())
        words = [word for word, _ in sorted(vocab['word_to_wid'].items(), key=lambda x: x[1])]
        config = yaml.safe_load((source / 'configs/variants/mutan_noatt_resnet18.yaml').read_text())
        # The trained checkpoint includes embedding and GRU parameters. Construct
        # their original classes, skip unused pretraining downloads, strict-load.
        old_embedding = skipthoughts.AbstractSkipThoughts._load_embedding
        old_rnn = skipthoughts.BayesianUniSkip._load_rnn
        skipthoughts.AbstractSkipThoughts._load_embedding = lambda self: torch.nn.Embedding(len(self.vocab) + 1, 620, padding_idx=0)
        skipthoughts.BayesianUniSkip._load_rnn = lambda self: BayesianGRU(620, 2400, dropout=self.dropout)
        try:
            model = models.factory(config['model'], words, vocab['aid_to_ans'])
        finally:
            skipthoughts.AbstractSkipThoughts._load_embedding = old_embedding
            skipthoughts.BayesianUniSkip._load_rnn = old_rnn
        state = torch.load(weight, weights_only=True, map_location='cpu')
        model.load_state_dict(state, strict=True)
        assert all(torch.equal(v.cpu(), state[k]) for k, v in model.state_dict().items())
        model.eval()
        source_text = (source / 'vqa/datasets/vqa_processed.py').read_text()
        function = next(n for n in ast.parse(source_text).body if isinstance(n, ast.FunctionDef) and n.name == 'tokenize_mcb')
        namespace = {'re': re}
        exec(compile(ast.Module(body=[function], type_ignores=[]), 'upstream-tokenize_mcb', 'exec'), namespace)

        def operator(inputs):
            feature = np.load(io.BytesIO(inputs['vision']), allow_pickle=False)
            if feature.shape != (512,) or feature.dtype != np.float32 or not np.isfinite(feature).all():
                raise ValueError('Invalid feature tensor')
            tokens = namespace['tokenize_mcb'](inputs['speech'].decode())[:26]
            if not tokens:
                raise ValueError('ASR produced empty question')
            ids = torch.tensor([[vocab['word_to_wid'].get(w, vocab['word_to_wid']['UNK']) for w in tokens]], dtype=torch.long)
            with torch.inference_mode():
                scores = model(torch.from_numpy(feature).unsqueeze(0), ids).softmax(-1)
                values, indices = scores.topk(5)
            result = {'answers': [vocab['aid_to_ans'][i] for i in indices[0].tolist()],
                      'answer_ids': indices[0].tolist(), 'scores': values[0].tolist()}
            return json.dumps(result, separators=(',', ':')).encode()
        metadata.update(model_sha256=sha(weight), vocab_sha256=sha(vocab_path), model='MutanNoAtt-resnet18',
                        strict_state_keys=len(state), all_checkpoint_tensors_equal=True,
                        initialization_change='skip unused pretrained initialization, strict trained state_dict replaces every tensor')
    else:
        raise ValueError('Unknown task')
    metadata['load_ms'] = (time.perf_counter() - started) * 1000
    return operator, metadata
