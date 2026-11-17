"""ManulX core: encoder and decoder.

Protocol specification: PROTOCOL.md (English), PROTOCOL.ru.md (Russian).
"""
import math
import sys
import wave

import numpy as np
from PIL import Image
from scipy.signal import firwin, oaconvolve, resample_poly

# ---------------- константы протокола ----------------
F_BLACK, F_WHITE = 1500.0, 2300.0
F_LEADER = 1900.0
F_SYNC = 1200.0
F_MARK = 1100.0

T_LEADER = 0.300
T_BREAK = 0.010
T_HSEP = 0.020
T_HSTART = 0.030
T_HBIT = 0.005
N_HCOPIES = 3
T_POST_HDR = 0.010
T_TAIL = 0.050
T_GAP_SILENCE = 0.300

T_SYNC = 0.005
T_PORCH = 0.001
T_LBIT = 0.001
N_LBITS = 13  # 12 бит номера + чётность
T_MARK = 0.001
T_GUARD = 0.0003  # защитный интервал: соседний пиксель удерживается у меток и краёв строки

MAGIC, VERSION = 0xA, 1
MODES = {"gray": 0, "yuv": 1, "rgb": 2}
MODE_NAMES = {v: k for k, v in MODES.items()}
CALL_CHARS = " ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789/-."
HEADER_FIELDS = [  # (имя, бит)
    ("magic", 4), ("version", 4), ("mode", 2), ("pt_code", 8), ("marker", 8),
    ("tile_w", 12), ("tile_h", 12), ("tile_x", 12), ("tile_y", 12),
    ("full_w", 12), ("full_h", 12), ("image_id", 8), ("call", 36),
]
N_HBITS = sum(b for _, b in HEADER_FIELDS) + 16
EOT_MAGIC = 0x5
N_EBITS = 32
N_ECOPIES = 2
T_HCOPY = T_HSEP + T_HSTART + N_HBITS * T_HBIT
T_ECOPY = T_HSEP + T_HSTART + N_EBITS * T_HBIT
T_EOT = N_ECOPIES * T_ECOPY


def crc16_ccitt(bits):
    crc = 0xFFFF
    for b in bits:
        top = (crc >> 15) & 1
        crc = (crc << 1) & 0xFFFF
        if top ^ b:
            crc ^= 0x1021
    return crc


def crc8(bits):
    crc = 0
    for b in bits:
        top = (crc >> 7) & 1
        crc = (crc << 1) & 0xFF
        if top ^ b:
            crc ^= 0x07
    return crc


def eot_to_bits(image_id, n_lines):
    body = int_to_bits(EOT_MAGIC, 4) + int_to_bits(image_id & 0xFF, 8) + int_to_bits(n_lines, 12)
    return body + int_to_bits(crc8(body), 8)


def bits_to_eot(bits):
    body = list(bits[:24])
    if crc8(body) != bits_to_int(bits[24:32]) or bits_to_int(body[:4]) != EOT_MAGIC:
        return None
    return dict(image_id=bits_to_int(body[4:12]), lines=bits_to_int(body[12:24]))


def int_to_bits(v, n):
    return [(v >> (n - 1 - i)) & 1 for i in range(n)]


def bits_to_int(bits):
    v = 0
    for b in bits:
        v = (v << 1) | int(b)
    return v


def encode_call(call):
    call = (call or "").upper()[:6].ljust(6)
    v = 0
    for ch in call:
        v = (v << 6) | (CALL_CHARS.index(ch) if ch in CALL_CHARS else 0)
    return v


def decode_call(v):
    chars = []
    for i in range(6):
        idx = (v >> (6 * (5 - i))) & 0x3F
        chars.append(CALL_CHARS[idx] if idx < len(CALL_CHARS) else "?")
    return "".join(chars).strip()


def header_to_bits(h):
    bits = []
    for name, n in HEADER_FIELDS:
        bits += int_to_bits(h[name], n)
    return bits + int_to_bits(crc16_ccitt(bits), 16)


def bits_to_header(bits):
    body, crc = bits[:-16], bits_to_int(bits[-16:])
    if crc16_ccitt(body) != crc:
        return None
    h, i = {}, 0
    for name, n in HEADER_FIELDS:
        h[name] = bits_to_int(body[i:i + n])
        i += n
    if h["magic"] != MAGIC or h["version"] != VERSION or h["mode"] not in MODE_NAMES:
        return None
    return h


def pt_seconds(h):
    return (h["pt_code"] + 1) * 5e-6


def payload_len(mode, w):
    cw = (w + 1) // 2
    return {0: w, 1: w + cw, 2: 3 * w}[mode]


def n_markers(mode, w, marker):
    n = payload_len(mode, w)
    return 0 if marker == 0 else (n - 1) // marker


def line_duration(h):
    w, mode, M = h["tile_w"], h["mode"], h["marker"]
    return (T_SYNC + T_PORCH + N_LBITS * T_LBIT + 2 * T_GUARD
            + payload_len(mode, w) * pt_seconds(h) + n_markers(mode, w, M) * (T_MARK + 2 * T_GUARD))


# ---------------- цвет ----------------
def rgb_to_ycc(rgb):
    r, g, b = [rgb[..., i].astype(np.float64) for i in range(3)]
    y = 0.299 * r + 0.587 * g + 0.114 * b
    cb = 128 - 0.168736 * r - 0.331264 * g + 0.5 * b
    cr = 128 + 0.5 * r - 0.418688 * g - 0.081312 * b
    return y, cb, cr


def ycc_to_rgb(y, cb, cr):
    r = y + 1.402 * (cr - 128)
    g = y - 0.344136 * (cb - 128) - 0.714136 * (cr - 128)
    b = y + 1.772 * (cb - 128)
    return np.clip(np.stack([r, g, b], -1), 0, 255).round().astype(np.uint8)


def half_width(c):
    """Усреднение пар пикселей по горизонтали (ceil(w/2))."""
    if c.shape[-1] % 2:
        c = np.concatenate([c, c[..., -1:]], -1)
    return 0.5 * (c[..., 0::2] + c[..., 1::2])


# ---------------- кодер ----------------
class ToneBuilder:
    def __init__(self):
        self.f, self.d = [], []

    def tone(self, f, d):
        self.f.append(f)
        self.d.append(d)

    def tones(self, fs, d):
        fs = np.asarray(fs, dtype=np.float64)
        self.f.append(fs)
        self.d.append(np.full(len(fs), d))

    def render(self, rate, amp=0.7):
        f = np.concatenate([np.atleast_1d(np.asarray(x, dtype=np.float64)) for x in self.f])
        d = np.concatenate([np.atleast_1d(np.asarray(x, dtype=np.float64)) for x in self.d])
        ends = np.cumsum(d)
        n = int(math.ceil(ends[-1] * rate))
        t = (np.arange(n) + 0.5) / rate
        idx = np.minimum(np.searchsorted(ends, t, side="right"), len(f) - 1)
        freq = f[idx]
        amp_arr = np.where(freq > 0, amp, 0.0)  # f=0 -> тишина
        phase = 2 * np.pi * np.cumsum(freq) / rate
        return (amp_arr * np.sin(phase)).astype(np.float32)


def value_to_freq(v):
    return F_BLACK + (np.clip(v, 0, 255) / 255.0) * (F_WHITE - F_BLACK)


def line_payload(mode, r, planes):
    if mode == 0:
        return planes[0][r]
    if mode == 1:
        y, cb_h, cr_h = planes
        pair = r // 2
        c = cr_h[pair] if r % 2 == 0 else cb_h[pair]
        return np.concatenate([y[r], c])
    return np.concatenate([planes[0][r], planes[1][r], planes[2][r]])


def encode_tile(tb, rgb, h, n_send=None):
    mode, pt, M = h["mode"], pt_seconds(h), h["marker"]
    th = rgb.shape[0]
    # лидер
    tb.tone(F_LEADER, T_LEADER)
    tb.tone(F_SYNC, T_BREAK)
    tb.tone(F_LEADER, T_LEADER)
    hb = header_to_bits(h)
    for _ in range(N_HCOPIES):
        tb.tone(F_LEADER, T_HSEP)
        tb.tone(F_SYNC, T_HSTART)
        tb.tones([F_WHITE if b else F_BLACK for b in hb], T_HBIT)
    tb.tone(F_LEADER, T_POST_HDR)

    if mode == 0:
        y, _, _ = rgb_to_ycc(rgb)
        planes = (y,)
    elif mode == 1:
        y, cb, cr = rgb_to_ycc(rgb)
        if th % 2:
            cb = np.vstack([cb, cb[-1:]])
            cr = np.vstack([cr, cr[-1:]])
        cb_h = half_width(0.5 * (cb[0::2] + cb[1::2]))
        cr_h = half_width(0.5 * (cr[0::2] + cr[1::2]))
        planes = (y, cb_h, cr_h)
    else:
        planes = tuple(rgb[..., i].astype(np.float64) for i in range(3))

    n_send = th if n_send is None else min(th, n_send)
    for r in range(n_send):
        tb.tone(F_SYNC, T_SYNC)
        tb.tone(F_BLACK, T_PORCH)
        nb = int_to_bits(r, 12)
        nb.append(sum(nb) % 2)
        tb.tones([F_WHITE if b else F_BLACK for b in nb], T_LBIT)
        freqs = value_to_freq(line_payload(mode, r, planes))
        tb.tone(freqs[0], T_GUARD)
        step = M if M else len(freqs)
        for s in range(0, len(freqs), step):
            if s:
                tb.tone(freqs[s - 1], T_GUARD)
                tb.tone(F_MARK, T_MARK)
                tb.tone(freqs[s], T_GUARD)
            tb.tones(freqs[s:s + step], pt)
        tb.tone(freqs[-1], T_GUARD)
    eb = eot_to_bits(h["image_id"], n_send)
    for _ in range(N_ECOPIES):
        tb.tone(F_LEADER, T_HSEP)
        tb.tone(F_SYNC, T_HSTART)
        tb.tones([F_WHITE if b else F_BLACK for b in eb], T_HBIT)
    tb.tone(F_LEADER, T_TAIL)


class ManulXError(Exception):
    pass


def prepare_image(img, max_side=0):
    """PIL.Image -> RGB-массив (с уменьшением, если большая сторона > max_side)."""
    img = img.convert("RGB")
    if max_side and max(img.size) > max_side:
        k = max_side / max(img.size)
        img = img.resize((max(1, round(img.width * k)), max(1, round(img.height * k))), Image.LANCZOS)
    return np.asarray(img)


def plan(W, H, tile, mode, pt_us, marker, call="", image_id=1, stream=False):
    """Список заголовков тайлов и полная длительность передачи (с).
    stream=True — высота не пишется в заголовок (tile_h = full_h = 0), тайлы только по ширине."""
    tw, th = tile if tile else (W, H)
    if stream:
        th = H
    if max(W, H, tw, th) > 4095:
        raise ManulXError("Maximum side is 4095 pixels (downscale the image or use tiles).")
    if min(W, H, tw, th) < 1:
        raise ManulXError("Empty image or tile.")
    pt_code = int(round(pt_us / 5.0)) - 1
    if not 0 <= pt_code <= 255:
        raise ManulXError("Time per sample must be within 5..1280 us.")
    if not 0 <= marker <= 255:
        raise ManulXError("Marker interval must be within 0..255.")
    mode_id = MODES[mode] if isinstance(mode, str) else mode
    headers, total = [], 0.0
    for ty in range(0, H, th):
        for tx in range(0, W, tw):
            h = dict(magic=MAGIC, version=VERSION, mode=mode_id, pt_code=pt_code,
                     marker=marker, tile_w=min(tw, W - tx), tile_h=min(th, H - ty),
                     tile_x=tx, tile_y=ty, full_w=W, full_h=H,
                     image_id=image_id & 0xFF, call=encode_call(call))
            h["_rows"] = h["tile_h"]  # реальная высота тайла (для кодера)
            if stream:
                h["tile_h"] = h["full_h"] = 0
            if headers:
                total += T_GAP_SILENCE
            total += tile_duration(h)
            headers.append(h)
    return headers, total


def tile_duration(h):
    return (2 * T_LEADER + T_BREAK + N_HCOPIES * T_HCOPY + T_POST_HDR
            + h.get("_rows", h["tile_h"]) * line_duration(h) + T_EOT + T_TAIL)


def encode_audio(img, mode="yuv", pt_us=275, marker=64, tile=None, max_side=0,
                 call="", image_id=1, rate=48000, log=print, progress=None, stream=False, stop_after=None):
    """PIL.Image -> (звук float32 [-1..1], частота дискретизации).
    stream — потоковый режим (высота не указывается); stop_after — досрочно
    остановить каждый тайл после N строк (для проверки сигнала окончания)."""
    rgb = prepare_image(img, max_side)
    H, W = rgb.shape[:2]
    headers, total = plan(W, H, tile, mode, pt_us, marker, call, image_id, stream)
    tb = ToneBuilder()
    for i, h in enumerate(headers):
        if i:
            tb.tone(0.0, T_GAP_SILENCE)
        x, y = h["tile_x"], h["tile_y"]
        hw = {k: v for k, v in h.items() if not k.startswith("_")}
        encode_tile(tb, rgb[y:y + h["_rows"], x:x + h["tile_w"]], hw, stop_after)
        log(f"  tile {i + 1}: {h['tile_w']}x{h['_rows']}{' (stream)' if stream else ''} @ ({x},{y}) - {tile_duration(h):.1f} s")
        if progress:
            progress((i + 1) / len(headers) * 0.8)
    audio = tb.render(rate)
    if progress:
        progress(1.0)
    log(f"ManulX: {W}x{H}, mode {MODE_NAMES[headers[0]['mode']]}, {pt_us:.0f} us/sample, "
        f"marker every {marker}, tiles {len(headers)}, duration {len(audio) / rate:.1f} s")
    return audio, rate


def write_wav(path, audio, rate):
    with wave.open(path, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(int(rate))
        wf.writeframes((np.clip(audio, -1, 1) * 32767).astype("<i2").tobytes())


# ---------------- декодер ----------------
WORK_RATE = 24000
F_CENTER = 1700.0


def read_wav(path):
    with wave.open(path, "rb") as wf:
        rate, nch, sw = wf.getframerate(), wf.getnchannels(), wf.getsampwidth()
        raw = wf.readframes(wf.getnframes())
    if sw == 2:
        x = np.frombuffer(raw, "<i2").astype(np.float64) / 32768
    elif sw == 1:
        x = (np.frombuffer(raw, np.uint8).astype(np.float64) - 128) / 128
    elif sw == 4:
        x = np.frombuffer(raw, "<i4").astype(np.float64) / 2 ** 31
    elif sw == 3:
        b = np.frombuffer(raw, np.uint8).reshape(-1, 3)
        v = (b[:, 0].astype(np.int32) | (b[:, 1].astype(np.int32) << 8) | (b[:, 2].astype(np.int32) << 16))
        v = np.where(v >= 1 << 23, v - (1 << 24), v)
        x = v / 2 ** 23
    else:
        raise ValueError("Unsupported WAV sample width")
    if nch > 1:
        x = x.reshape(-1, nch).mean(1)
    return x, rate


def moving_avg(x, n):
    n = max(1, int(n))
    if n == 1:
        return x
    k = np.ones(n) / n
    return np.convolve(x, k, mode="same")


def runs(mask):
    """Список (start, end) непрерывных участков True."""
    m = np.concatenate([[False], mask, [False]]).astype(np.int8)
    d = np.diff(m)
    return list(zip(np.where(d == 1)[0], np.where(d == -1)[0]))


class Demod:
    """FM-демодулятор: перенос на 1700 Гц, ФНЧ, затем частота как угол
    усреднённого комплексного произведения z[n+1]*conj(z[n]).
    Усреднение комплексного произведения (а не уже вычисленной частоты)
    устойчиво к шумовым выбросам — слабые/шумовые участки весят меньше."""

    def __init__(self, x, rate):
        g = math.gcd(int(rate), WORK_RATE)
        if rate != WORK_RATE:
            x = resample_poly(x, WORK_RATE // g, int(rate) // g)
        self.fs = fs = WORK_RATE
        n = np.arange(len(x))
        z = x * np.exp(-2j * np.pi * F_CENTER * n / fs)
        z = oaconvolve(z, firwin(121, 1500, fs=fs), mode="same")
        d = z[1:] * np.conj(z[:-1])  # d[k] — на интервале [k, k+1)
        d /= np.sqrt(np.abs(d)) + 1e-12  # мягкая нормировка (вес ~ амплитуде)
        self.D = np.concatenate([[0j], np.cumsum(d)])
        self.n = len(d)
        self.dur = self.n / fs
        self.f03 = self._smooth(0.0003)
        self.f1 = self._smooth(0.001)
        self.f4 = self._smooth(0.004)
        self.f10 = self._smooth(0.010)

    def _to_f(self, c):
        return np.angle(c) * self.fs / (2 * np.pi) + F_CENTER

    def _smooth(self, t):
        k = max(1, int(round(t * self.fs))) | 1  # нечётная длина: центр окна в (i+0.5)/fs
        c = np.zeros(self.n, complex)
        h = k // 2
        idx = np.arange(self.n)
        lo, hi = np.clip(idx - h, 0, self.n), np.clip(idx - h + k, 0, self.n)
        c = self.D[hi] - self.D[lo]
        return self._to_f(c)

    def mean(self, a, b):
        """Средняя частота на интервалах времени [a, b] (векторно, дробные границы)."""
        a = np.clip(np.asarray(a, float) * self.fs, 0, self.n)
        b = np.clip(np.asarray(b, float) * self.fs, 0, self.n)
        return self._to_f(self._D_at(b) - self._D_at(a))

    def _D_at(self, x):
        """Линейная интерполяция накопленной суммы D в дробной позиции x (в отсчётах)."""
        i = np.minimum(np.floor(x).astype(np.int64), self.n - 1)
        fr = x - i
        return self.D[i] + (self.D[i + 1] - self.D[i]) * fr

    def median(self, a, b):
        """Медиана мгновенной частоты на [a, b] — устойчива к щелчкам (для битов)."""
        i0, i1 = max(0, int(a * self.fs)), min(self.n, int(b * self.fs) + 1)
        return float(np.median(self.f03[i0:i1])) if i1 > i0 else F_CENTER

    def crossing(self, t_from, t_to, level, rising, src=None):
        """Время первого пересечения уровня (по f03 или src) в окне, с субсемпловой точностью."""
        src = self.f03 if src is None else src
        i0, i1 = max(0, int(t_from * self.fs)), min(len(src) - 1, int(t_to * self.fs))
        s = src[i0:i1 + 1]
        if len(s) < 2:
            return None
        if rising:
            k = np.where((s[:-1] < level) & (s[1:] >= level))[0]
        else:
            k = np.where((s[:-1] > level) & (s[1:] <= level))[0]
        if not len(k):
            return None
        k = k[0]
        frac = (level - s[k]) / (s[k + 1] - s[k])
        return (i0 + k + 0.5 + frac) / self.fs

    def step_edge(self, t_from, t_to, f_before, f_after, w_before, w_after):
        """Момент перехода f_before -> f_after в окне [t_from, t_to]: минимум квадратичной
        ошибки модели «ступенька» (w_before до, w_after после). Устойчиво к дрожанию частоты."""
        fs = self.fs
        i0 = int(t_from * fs) - int(w_before * fs)
        i1 = int(t_to * fs) + int(w_after * fs) + 1
        if i0 < 0 or i1 > self.n:
            return None
        f = np.clip(self.f03[i0:i1], 800, 2800)
        cb = np.concatenate([[0], np.cumsum((f - f_before) ** 2)])
        ca = np.concatenate([[0], np.cumsum((f - f_after) ** 2)])
        nb, na = int(w_before * fs), int(w_after * fs)
        ts = np.arange(nb, len(f) - na + 1)
        cost = (cb[ts] - cb[ts - nb]) + (ca[ts + na] - ca[ts])
        k = int(np.argmin(cost))
        # параболическая интерполяция минимума
        frac = 0.0
        if 0 < k < len(cost) - 1:
            y0, y1, y2 = cost[k - 1], cost[k], cost[k + 1]
            den = y0 - 2 * y1 + y2
            if den > 0:
                frac = 0.5 * (y0 - y2) / den
        return (i0 + ts[k] + frac) / fs


def read_bits(dm, start, nbits, T, f0=F_BLACK, f1=F_WHITE):
    """Чтение FSK-битов с подстройкой фазы на каждом переходе 0<->1 (как в UART),
    чтобы плавающая скорость не накапливала сдвиг. Возвращает средние частоты бит."""
    thr = (f0 + f1) / 2
    t, prev, out = start, None, []
    for _ in range(nbits):
        v = dm.median(t + 0.2 * T, t + 0.8 * T)
        b = v > thr
        if prev is not None and b != prev:
            fa, fb = (f1, f0) if prev else (f0, f1)
            e = dm.step_edge(t - 0.4 * T, t + 0.4 * T, fa, fb, 0.5 * T, 0.5 * T)
            if e is not None:
                t = t + 0.6 * (e - t)
                v = dm.median(t + 0.2 * T, t + 0.8 * T)
                b = v > thr
        out.append(v)
        prev = b
        t += T
    return np.array(out)


def find_headers(dm, t_start):
    """Ищет лидер, затем читает 3 копии заголовка (каждую по своему фронту).
    Возвращает (header, t_hdr_end) или (None, None)."""
    fs = dm.fs
    i_start = int(t_start * fs)
    thr = (F_BLACK + F_WHITE) / 2
    # ищем стартовый импульс 1200 Гц (~30 мс), перед которым ~100 мс преобладает 1900 Гц.
    # Доля, а не непрерывность — так провалы и щелчки в лидере не мешают.
    start_pulses = np.abs(dm.f10[i_start:] - F_SYNC) < 100
    for a, b in runs(start_pulses):
        if (b - a) / fs < 0.012:
            continue
        ts = (i_start + a) / fs
        pre = dm.f1[max(0, int((ts - 0.12) * fs)): int((ts - 0.01) * fs)]
        if not len(pre) or np.mean(np.abs(pre - F_LEADER) < 100) < 0.6:
            continue
        t0 = dm.step_edge(ts - 0.012, ts + 0.012, F_LEADER, F_SYNC, 0.015, 0.025)
        if t0 is None:
            continue
        # убеждаемся, что дальше действительно 1200 Гц ~30 мс (а не просто конец лидера)
        if abs(dm.mean(t0 + 0.005, t0 + 0.025) - F_SYNC) > 120:
            continue
        copies, last_edge = [], t0
        for k in range(N_HCOPIES):
            exp = t0 + k * T_HCOPY
            e = t0 if k == 0 else dm.step_edge(exp - 0.020, exp + 0.020, F_LEADER, F_SYNC, 0.015, 0.025)
            if e is None or abs(dm.mean(e + 0.005, e + 0.025) - F_SYNC) > 120 \
                    or abs(dm.mean(e - 0.015, e - 0.004) - F_LEADER) > 150:
                e = exp
            last_edge = e
            v = read_bits(dm, e + T_HSTART, N_HBITS, T_HBIT)
            copies.append(v)
            h = bits_to_header([int(x > thr) for x in v])
            if h:
                return h, t0 - T_HSEP + N_HCOPIES * T_HCOPY
        # побитовое голосование (среднее частот по копиям)
        v = np.median(copies, axis=0)
        h = bits_to_header([int(x > thr) for x in v])
        if h:
            return h, t0 - T_HSEP + N_HCOPIES * T_HCOPY
    return None, None


def find_eots(dm, t_from, t_to, image_id=None):
    """Все сигналы окончания в [t_from, t_to]: список (eot, t_start, t_end).
    Признак: импульс 1200 Гц ~30 мс, перед которым ~20 мс 1900 Гц; затем 32 бита с CRC-8.
    Две копии; если обе не прошли CRC — побитовое голосование."""
    fs = dm.fs
    i0, i1 = max(0, int(t_from * fs)), min(dm.n, int(t_to * fs))
    if i1 - i0 < int(0.05 * fs):
        return []
    thr = (F_BLACK + F_WHITE) / 2
    found, skip_until = [], -1.0
    for a, b in runs(np.abs(dm.f10[i0:i1] - F_SYNC) < 100):
        if (b - a) / fs < 0.012:
            continue
        ts = (i0 + a) / fs
        if ts < skip_until:
            continue
        pre = dm.f1[max(0, int((ts - 0.022) * fs)): int((ts - 0.003) * fs)]
        if not len(pre) or np.mean(np.abs(pre - F_LEADER) < 120) < 0.5:
            continue
        e0 = dm.step_edge(ts - 0.012, ts + 0.012, F_LEADER, F_SYNC, 0.015, 0.025)
        if e0 is None:
            continue
        copies, res = [], None
        for k in range(N_ECOPIES):
            exp = e0 + k * T_ECOPY
            e = e0 if k == 0 else (dm.step_edge(exp - 0.01, exp + 0.01, F_LEADER, F_SYNC, 0.015, 0.025) or exp)
            v = read_bits(dm, e + T_HSTART, N_EBITS, T_HBIT)
            copies.append(v)
            res = bits_to_eot([int(x > thr) for x in v])
            if res:
                break
        if res is None:
            res = bits_to_eot([int(x > thr) for x in np.median(copies, axis=0)])
        if res is None:
            continue
        if image_id is not None and res["image_id"] != image_id:
            continue
        start = e0 - T_HSEP
        # если это вторая копия — начало EOT на одну копию раньше
        prev = dm.f1[max(0, int((start - T_ECOPY + T_HSEP + 0.005) * fs)): int((start - T_ECOPY + T_HSEP + 0.025) * fs)]
        if len(prev) and np.mean(np.abs(prev - F_SYNC) < 120) > 0.6:
            start -= T_ECOPY
        end = start + T_EOT
        found.append((res, start, end))
        skip_until = end + 0.01
    return found


def find_marker(dm, m_nom, rate, wide=False):
    """Центр метки 1100 Гц около ожидаемого начала m_nom: минимум 1-мс скользящего
    среднего частоты (пиксели >= 1500 Гц, поэтому минимум приходится на метку)."""
    fs = dm.fs
    c_nom = m_nom + 0.5 * T_MARK * rate
    span = 0.015 if wide else 0.0012
    lo, hi = max(1, int((c_nom - span) * fs)), min(dm.n - 1, int((c_nom + span) * fs))
    w = dm.f1[lo:hi]
    if len(w) < 3:
        return None
    k = int(np.argmin(w))
    if w[k] > 1300 or k == 0 or k == len(w) - 1:
        return None
    y0, y1, y2 = w[k - 1], w[k], w[k + 1]
    den = y0 - 2 * y1 + y2
    frac = 0.5 * (y0 - y2) / den if den > 0 else 0.0
    return (lo + k + frac + 0.5) / fs


def decode_lines(dm, h, t_hdr_end, t_search_end=None):
    """Чтение строк тайла. Возвращает (rgb, stats, t_end).
    t_search_end — где начинается следующая передача (или конец записи)."""
    fs = dm.fs
    mode, w, th_hdr, M = h["mode"], h["tile_w"], h["tile_h"], h["marker"]
    stream = th_hdr == 0
    pt = pt_seconds(h)
    L = line_duration(h)
    n_payload = payload_len(mode, w)
    t_lines = t_hdr_end + T_POST_HDR
    edge0_nom = t_lines + T_SYNC
    t_search_end = dm.dur if t_search_end is None else min(t_search_end, dm.dur)

    # 0. сигнал окончания: где закончились строки и сколько их было
    t_eot_to = t_search_end if stream else min(t_search_end, t_lines + th_hdr * L * 1.05 + 1.5)
    eots = find_eots(dm, t_lines, t_eot_to, h["image_id"])
    eot = eots[0] if eots else None
    if eot:
        th = eot[0]["lines"] if stream else min(th_hdr, eot[0]["lines"])
        t_limit = eot[1] + 0.002
    elif stream:
        th = int(max(1, min(4095, (t_search_end - t_lines) / L + 1)))
        t_limit = t_search_end
    else:
        th = th_hdr
        t_limit = min(t_search_end, t_lines + th * L * 1.03 + 0.1)
    if th <= 0:
        return np.zeros((0, w, 3), np.uint8), dict(lines_found=0, lines_pred=0, lines_total=0, marks_found=0,
                                                   marks_total=0, rate=1.0, eot=bool(eot), eot_lines=0,
                                                   cut_at=None, stream=stream, declared=th_hdr), \
            (eot[2] if eot else t_lines)

    # 1. кандидаты синхроимпульсов
    i0, i1 = int(t_lines * fs) - int(0.003 * fs), min(len(dm.f4), int(t_limit * fs))
    # 4-мс среднее: пиксели (>=1500) и короткие метки не опускают его ниже ~1400,
    # а 5-мс синхроимпульс 1200 Гц — опускает, даже при дрожании частоты от кодека
    mask = dm.f4[i0:i1] < 1350
    cands = []
    for a, b in runs(mask):
        if (b - a) / fs < 0.0005:
            continue
        te = (i0 + b) / fs
        edge = dm.step_edge(te - 0.003, te + 0.003, F_SYNC, F_BLACK, 0.004, 0.0009)
        if edge is None:
            continue
        v = read_bits(dm, edge + T_PORCH, N_LBITS, T_LBIT)
        bits = [int(x > (F_BLACK + F_WHITE) / 2) for x in v]
        num = bits_to_int(bits[:12]) if sum(bits[:12]) % 2 == bits[12] else None
        cands.append((edge, num))

    # 2. грубая оценка периода строк (перебор наклона ±1%, медиана сдвига)
    good = [(n, e) for e, n in cands if n is not None and n < th]
    b_fit = L
    if len(good) >= 2:
        n_arr = np.array([g[0] for g in good], float)
        e_arr = np.array([g[1] for g in good])
        best = None
        for b_try in L * np.linspace(0.99, 1.01, 81):
            off = e_arr - b_try * n_arr
            score = np.sum(np.abs(off - np.median(off)) < 0.01)
            if best is None or score > best[0]:
                best = (score, b_try)
        b_fit = best[1]
        off = e_arr - b_fit * n_arr
        inl = np.abs(off - np.median(off)) < 0.01
        if inl.sum() >= 2 and np.ptp(n_arr[inl]) > 0:
            b_fit = float(np.polyfit(n_arr[inl], e_arr[inl], 1)[0])
    rate = float(np.clip(b_fit / L, 0.95, 1.05))

    # 3. привязка по согласованности последовательности: номер строки принимается,
    #    если у соседнего (по времени) синхроимпульса номер отличается ровно на
    #    round(dt / период). Скачки времени (мс) на это не влияют, ошибочные номера отсеиваются.
    edges = [None] * th
    seq = sorted((e, n) for e, n in cands if n is not None and n < th)
    for i, (e, n) in enumerate(seq):
        votes = 0
        for j in (i - 2, i - 1, i + 1, i + 2):
            if 0 <= j < len(seq):
                ej, nj = seq[j]
                if nj - n == int(round((ej - e) / b_fit)) and nj != n:
                    votes += 1
        if votes >= 1 and edges[n] is None:
            edges[n] = e
    acc = np.array([k for k in range(th) if edges[k] is not None])

    def predict(k):
        if not len(acc):
            return edge0_nom + L * k
        j = np.searchsorted(acc, k)
        lo_ = acc[j - 1] if j > 0 else None
        hi_ = acc[j] if j < len(acc) else None
        olo = edges[lo_] - b_fit * lo_ if lo_ is not None else None
        ohi = edges[hi_] - b_fit * hi_ if hi_ is not None else None
        o = olo if ohi is None else ohi if olo is None else olo + (ohi - olo) * (k - lo_) / (hi_ - lo_)
        return b_fit * k + o

    # синхро без валидного номера — по предсказанному времени
    for e, n in cands:
        if n is None or n >= th:
            k = int(round((e - predict(0)) / b_fit))
            for kk in (k - 1, k, k + 1):
                if 0 <= kk < th and edges[kk] is None and abs(e - predict(kk)) < 0.003:
                    edges[kk] = e
                    break
    found = sum(e is not None for e in edges)
    acc = np.array([k for k in range(th) if edges[k] is not None])
    last_seen = int(acc.max()) if len(acc) else -1
    # без сигнала окончания строки после последней найденной считаем непереданными
    cut_at = None
    if not eot:
        if stream:
            th = max(1, last_seen + 1)
            edges = edges[:th]
        elif last_seen < th - 3:
            cut_at = last_seen + 1
    n_valid = th if cut_at is None else cut_at
    # строки без синхро, но с найденными соседями (±2) — читаем по предсказанию
    predicted = 0
    for k in range(n_valid):
        if edges[k] is None and len(acc) and np.min(np.abs(acc - k)) <= 2:
            edges[k] = predict(k)
            predicted += 1
    for k in range(n_valid, th):
        edges[k] = None
    a_fit = predict(0)

    # 4. чтение строк
    vals = np.zeros((th, n_payload))
    ok_lines = np.zeros(th, bool)
    marks_found = marks_total = 0
    for r in range(th):
        e = edges[r]
        if e is None:
            continue
        ok_lines[r] = True
        s = e + (T_PORCH + N_LBITS * T_LBIT + T_GUARD) * rate
        idx = 0
        while idx < n_payload:
            n = min(M, n_payload - idx) if M else n_payload
            seg_pt = pt * rate
            resync = None
            last = idx + n >= n_payload
            if not last:
                m_nom = s + n * seg_pt + T_GUARD * rate
                marks_total += 1
                center = find_marker(dm, m_nom, rate)
                jump = None
                if center is None:
                    center = find_marker(dm, m_nom, rate, wide=True)
                    jump = center
                if center is not None:
                    m_start = center - 0.5 * T_MARK * rate
                    d = (m_start - T_GUARD * rate - s) / n
                    if jump is None and abs(d / seg_pt - 1) < 0.1:
                        # мелкие отклонения (< 0.1 мс) — погрешность оценки, не трогаем
                        if abs(m_start - m_nom) > 0.0001:
                            seg_pt = d
                        marks_found += 1
                    elif jump is not None:
                        marks_found += 1
                        resync = m_start  # скачок времени: этот сегмент номинально, следующий — от метки
            a_ = s + np.arange(n) * seg_pt
            f = dm.mean(a_ + 0.1 * seg_pt, a_ + 0.9 * seg_pt)
            vals[r, idx:idx + n] = (f - F_BLACK) / (F_WHITE - F_BLACK) * 255
            if resync is not None:
                s = resync + (T_MARK + T_GUARD) * rate
            else:
                s = s + n * seg_pt + (0 if last else (T_MARK + 2 * T_GUARD) * rate)
            idx += n
    vals = np.clip(vals, 0, 255)

    # 5. потерянные строки — копия ближайшей найденной (кроме непереданного хвоста)
    if found and ok_lines.sum() < n_valid:
        good_idx = np.where(ok_lines)[0]
        for r in np.where(~ok_lines[:n_valid])[0]:
            j = good_idx[np.argmin(np.abs(good_idx - r))]
            vals[r] = vals[j]

    # 6. сборка цвета
    if mode == 0:
        y = vals[:, :w]
        rgb = np.repeat(np.clip(y, 0, 255).round().astype(np.uint8)[..., None], 3, -1)
    elif mode == 2:
        rgb = np.clip(np.stack([vals[:, :w], vals[:, w:2 * w], vals[:, 2 * w:]], -1), 0, 255).round().astype(np.uint8)
    else:
        y = vals[:, :w]
        c = vals[:, w:]
        cr_rows, cb_rows = c[0::2], c[1::2]
        crok, cbok = ok_lines[0::2], ok_lines[1::2]
        npairs = (th + 1) // 2

        def pick(rows, okm, k):
            if k < len(rows) and okm[k]:
                return rows[k]
            oks = np.where(okm)[0]
            return rows[oks[np.argmin(np.abs(oks - k))]] if len(oks) else np.full(rows.shape[1], 128.0)

        cr = np.array([pick(cr_rows, crok, k) for k in range(npairs)])
        cb = np.array([pick(cb_rows, cbok, k) for k in range(npairs)]) if len(cb_rows) else np.full_like(cr, 128.0)
        cr_full = np.repeat(np.repeat(cr, 2, 0)[:th], 2, 1)[:, :w]
        cb_full = np.repeat(np.repeat(cb, 2, 0)[:th], 2, 1)[:, :w]
        rgb = ycc_to_rgb(y, cb_full, cr_full)
    if n_valid < th:
        rgb[n_valid:] = 40  # непереданный хвост — тёмно-серый

    stats = dict(lines_found=found, lines_pred=predicted, lines_total=th, marks_found=marks_found,
                 marks_total=marks_total, rate=rate, eot=bool(eot), eot_lines=eot[0]["lines"] if eot else None,
                 cut_at=cut_at, stream=stream, declared=th_hdr)
    if eot:
        t_end = eot[2]
    else:
        last = max((k for k in range(th) if edges[k] is not None), default=None)
        t_end = (edges[last] if last is not None else a_fit) + L
    return rgb, stats, t_end


def load_audio(path):
    """WAV — встроенными средствами; OGG/Opus (голосовые Telegram), FLAC, MP3 и др. —
    через soundfile, а если его нет или он не справился — через ffmpeg."""
    import os
    if not os.path.isfile(path):
        raise ManulXError(f"File not found: {path}")
    if path.lower().endswith(".wav"):
        try:
            return read_wav(path)
        except Exception:
            pass
    try:
        import soundfile as sf
        x, rate = sf.read(path, dtype="float64", always_2d=True)
        return x.mean(1), rate
    except Exception:
        pass
    import shutil, subprocess, tempfile
    ff = shutil.which("ffmpeg")
    if not ff:
        raise ManulXError("Cannot read the file. This format requires the soundfile package or ffmpeg in PATH.")
    fd, tmp = tempfile.mkstemp(suffix=".wav")
    os.close(fd)
    try:
        r = subprocess.run([ff, "-y", "-loglevel", "error", "-i", path, "-ac", "1", "-ar", "48000", tmp],
                           capture_output=True, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        if r.returncode != 0:
            raise ManulXError(f"Cannot read the file: {path}")
        return read_wav(tmp)
    finally:
        try:
            os.remove(tmp)
        except OSError:
            pass


def tile_status(st):
    """Человекочитаемое состояние тайла по статистике decode_lines."""
    if st["eot"]:
        if st["stream"]:
            return f"complete: stream, {st['lines_total']} lines, end-of-transmission received"
        if st["eot_lines"] is not None and st["eot_lines"] < st["declared"]:
            return f"complete: stopped early at {st['eot_lines']} of {st['declared']} lines (end-of-transmission received)"
        return "complete: end-of-transmission received"
    if st["cut_at"] is not None:
        return f"INTERRUPTED at line {st['cut_at']} of {st['lines_total']}: no end-of-transmission"
    if st["stream"]:
        return f"stream without end-of-transmission: {st['lines_total']} lines received, the end may be lost"
    return "all lines present, but end-of-transmission not detected"


def decode_samples(x, rate, log=print, progress=None):
    """Звук -> список изображений:
    [{'image': PIL.Image, 'id', 'w', 'h', 'tiles', 'call', 'complete', 'notes'}]."""
    if len(x) < rate * 0.5:
        raise ManulXError("Recording is too short.")
    dm = Demod(np.asarray(x, dtype=np.float64), rate)
    images = {}
    nxt = find_headers(dm, 0.0)
    while nxt[0] is not None:
        h, t_hdr = nxt
        t0 = t_hdr - N_HCOPIES * T_HCOPY + T_HSEP
        nxt = find_headers(dm, t_hdr + 0.05)
        # начало следующей передачи (лидер ~0.62 с до её первого заголовка)
        t_next = (nxt[1] - N_HCOPIES * T_HCOPY - 2 * T_LEADER - T_BREAK - 0.05) if nxt[0] is not None else dm.dur
        rgb, st, t_end = decode_lines(dm, h, t_hdr, t_next)
        key = (h["image_id"], h["full_w"], h["full_h"])
        if key not in images:
            images[key] = dict(canvas=np.full((max(h["full_h"], 1), h["full_w"], 3), 40, np.uint8),
                               call=decode_call(h["call"]), tiles=0, complete=True, notes=[], grow=h["full_h"] == 0)
        im = images[key]
        y0, x0 = h["tile_y"], h["tile_x"]
        if im["grow"] and y0 + rgb.shape[0] > im["canvas"].shape[0]:
            cv = np.full((y0 + rgb.shape[0], im["canvas"].shape[1], 3), 40, np.uint8)
            cv[:im["canvas"].shape[0]] = im["canvas"]
            im["canvas"] = cv
        cv = im["canvas"]
        hh, ww = min(rgb.shape[0], cv.shape[0] - y0), min(rgb.shape[1], cv.shape[1] - x0)
        if hh > 0 and ww > 0:
            cv[y0:y0 + hh, x0:x0 + ww] = rgb[:hh, :ww]
        im["tiles"] += 1
        status = tile_status(st)
        if not st["eot"]:
            im["complete"] = False
        im["notes"].append(f"tile @({x0},{y0}): {status}")
        hdr_h = "stream" if h["tile_h"] == 0 else h["tile_h"]
        full_h = "?" if h["full_h"] == 0 else h["full_h"]
        log(f"[{t0:7.2f} s] tile {h['tile_w']}x{hdr_h} @ ({x0},{y0}) of {h['full_w']}x{full_h}, "
            f"{MODE_NAMES[h['mode']]}, {pt_seconds(h) * 1e6:.0f} us, callsign '{decode_call(h['call'])}': "
            f"lines {st['lines_found']}/{st['lines_total']} (+{st['lines_pred']} interpolated), "
            f"markers {st['marks_found']}/{st['marks_total']}, time scale {st['rate']:.5f}")
        log(f"           {status}")
        if progress:
            progress(min(1.0, t_end / dm.dur))
    if progress:
        progress(1.0)
    if not images:
        raise ManulXError("No ManulX transmissions found.")
    return [dict(image=Image.fromarray(im["canvas"]), id=k[0], w=im["canvas"].shape[1], h=im["canvas"].shape[0],
                 tiles=im["tiles"], call=im["call"], complete=im["complete"], notes=im["notes"])
            for k, im in images.items()]


def eot_watch(x, rate):
    """Для автостопа записи: анализирует хвост записи. Возвращает
    (t_конца_последнего_EOT или None, есть_ли_после_него_новый_лидер)."""
    if len(x) < rate * 0.5:
        return None, False
    dm = Demod(np.asarray(x, dtype=np.float64), rate)
    eots = find_eots(dm, 0.0, dm.dur)
    if not eots:
        return None, False
    t_end = eots[-1][2]
    i = int((t_end + T_TAIL + 0.01) * dm.fs)
    after = np.abs(dm.f10[i:] - F_LEADER) < 80
    leader = any((b - a) / dm.fs >= 0.15 for a, b in runs(after))
    return t_end, leader
