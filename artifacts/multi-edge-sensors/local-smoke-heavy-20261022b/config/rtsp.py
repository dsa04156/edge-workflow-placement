"""MediaMTX configuration, PyAV (bundled FFmpeg/libx264) RTSP publisher and reader.

The publisher loops the scenario frames at a fixed fps with GOP = loop length
and no B-frames, so every loop starts with an IDR. The reader separates RTSP
session setup, demux waiting (network/pacing) and CPU decode per packet, and
identifies frames only by their barcode.
"""
import hashlib
import json
import time

SETUP_TIMEOUT_S = 10


def mediamtx_config(bind_ip, port=8554, api_port=9997):
    """Only RTSP over TCP and the local API; every other server disabled. JSON is valid YAML."""
    allow = [bind_ip+'/32', '127.0.0.0/8']
    return json.dumps(dict(
        logLevel='info', logDestinations=['stdout'], readTimeout='10s', writeTimeout='10s',
        authMethod='internal', authInternalUsers=[
            dict(user='any', ips=[], permissions=[dict(action='publish'), dict(action='read')]),
            dict(user='any', ips=allow, permissions=[dict(action='api')])],
        api=True, apiAddress=f'{bind_ip}:{api_port}', metrics=False, pprof=False, playback=False,
        rtsp=True, rtspTransports=['tcp'], rtspEncryption='no', rtspAddress=f'{bind_ip}:{port}',
        rtpAddress=f'{bind_ip}:{port+1000}', rtcpAddress=f'{bind_ip}:{port+1001}',
        rtmp=False, hls=False, webrtc=False, srt=False, moq=False, paths=dict(all_others={})), indent=1)


def publish(url, frames, stream, stop=None, log=None):
    """Encode frames[i % len(frames)] forever (or until stop()) to an RTSP URL, paced on the monotonic clock."""
    import av
    options = {'rtsp_transport': stream['transport']}
    container = av.open(url, mode='w', format='rtsp', options=options)
    video = container.add_stream(stream['encoder'], rate=stream['fps'])
    video.width, video.height, video.pix_fmt = stream['width'], stream['height'], stream['pix_fmt']
    video.options = dict(stream['encoder_options'])
    start, index = time.monotonic(), 0
    try:
        while stop is None or not stop():
            frame = av.VideoFrame.from_ndarray(frames[index % len(frames)], format='rgb24')
            frame.pts = index
            for packet in video.encode(frame):
                container.mux(packet)
            index += 1
            if log and index % len(frames) == 0:
                log(dict(event='loop', frames=index, elapsed_s=time.monotonic()-start))
            delay = start + index/stream['fps'] - time.monotonic()
            if delay > 0:
                time.sleep(delay)
    finally:
        container.close()


def read(url, wanted=None, max_frames=60, timeout_s=20, keep=None):
    """Decode an RTSP stream until every wanted seq was seen twice (or max_frames/timeout).

    Returns timing/codec evidence and, per barcode seq, decoded RGB sha256 values in arrival order.
    keep(seq, rgb) is called for each decoded frame with a valid barcode.
    """
    import av
    import scenario
    t0 = time.monotonic()
    # setup_ms includes FFmpeg stream probing (analyzeduration 1 s); probed packets are buffered, not lost.
    container = av.open(url, options={'rtsp_transport': 'tcp', 'timeout': str(SETUP_TIMEOUT_S*1000000),
                                      'analyzeduration': '1000000'}, timeout=SETUP_TIMEOUT_S)
    setup_ms = (time.monotonic()-t0)*1000
    stream = container.streams.video[0]
    codec = dict(name=stream.codec_context.name, width=stream.codec_context.width, height=stream.codec_context.height,
                 pix_fmt=stream.codec_context.pix_fmt, average_rate=str(stream.average_rate))
    seen, order, decode_ms, wait_ms, packets, packet_bytes, skipped = {}, [], [], [], 0, 0, 0
    first_frame_ms, last = None, time.monotonic()
    wanted = set(wanted) if wanted is not None else None
    try:
        for packet in container.demux(stream):
            now = time.monotonic()
            wait_ms.append((now-last)*1000)
            if packet.size == 0:
                last = now
                continue
            packets += 1
            packet_bytes += packet.size
            cpu = time.perf_counter_ns()
            frames = packet.decode()
            decode_ms.append((time.perf_counter_ns()-cpu)/1e6)
            for frame in frames:
                rgb = frame.to_ndarray(format='rgb24')
                seq = scenario.read_barcode(rgb)
                if seq is None:
                    skipped += 1
                    continue
                if first_frame_ms is None:
                    first_frame_ms = (time.monotonic()-t0)*1000
                digest = hashlib.sha256(rgb.tobytes()).hexdigest()
                seen.setdefault(seq, []).append(digest)
                order.append(seq)
                if keep:
                    keep(seq, rgb)
            last = time.monotonic()
            done = wanted is not None and all(len(seen.get(s, [])) >= 2 for s in wanted)
            if done or len(order) >= max_frames or time.monotonic()-t0 > timeout_s:
                break
    finally:
        container.close()
    return dict(url=url, setup_ms=setup_ms, first_barcode_frame_ms=first_frame_ms, total_ms=(time.monotonic()-t0)*1000,
                codec=codec, packets=packets, packet_bytes=packet_bytes, decoded_frames=len(order), skipped_frames=skipped,
                decode_ms_sum=sum(decode_ms), decode_ms_max=max(decode_ms, default=None),
                demux_wait_ms_sum=sum(wait_ms), seq_order=order, frame_sha256=seen,
                loop_consistent={str(s): len(set(h)) == 1 for s, h in seen.items() if len(h) >= 2},
                scope='RTSP/TCP session setup, demux wait (pacing+network) and CPU decode separated; monotonic, one host')
