# ManulX

[Русская версия](README.ru.md)

ManulX is a modular SSTV-like protocol for sending images over audio. Image size, color mode, timing and tiling are described in the header of every transmission, and the end of every transmission is marked by an end-of-transmission signal.

Protocol specification: [PROTOCOL.md](PROTOCOL.md).

## Installation

```
pip install manulx
```

From the source directory:

```
pip install .
```

Requirements: Python 3.10+, numpy, scipy, pillow.

WAV files are read without extra packages. To decode OGG/Opus, FLAC, MP3 and other formats, install the `formats` extra (soundfile) or have `ffmpeg` in `PATH`:

```
pip install "manulx[formats]"
```

## Usage

```
manulx encode image.png signal.wav
manulx decode signal.wav image.png
```

The same commands are available as `python -m manulx`.

### encode

```
manulx encode INPUT OUTPUT [options]
```

| Option | Default | Description |
|--------|---------|-------------|
| `--mode {yuv,rgb,gray}` | `yuv` | color mode |
| `--pt US` | `275` | time per sample, µs (5…1280) |
| `--marker N` | `64` | in-line marker every N samples; 0 = no markers |
| `--tile WxH` | — | split into tiles, e.g. `320x240` |
| `--max-side N` | — | downscale so that the longer side is at most N pixels |
| `--call CALL` | — | callsign, up to 6 characters |
| `--image-id N` | `1` | image number, 0…255 |
| `--rate HZ` | `48000` | sample rate of the WAV file |
| `--stream` | — | stream mode: the height is not sent, the end is marked by the end-of-transmission signal |
| `--stop-after N` | — | stop each tile after N lines (early stop announced by the end-of-transmission signal) |
| `-q`, `--quiet` | — | no progress output |

The maximum image or tile side is 4095 pixels.

### decode

```
manulx decode INPUT OUTPUT [-q]
```

`INPUT` is a recording; it may contain several transmissions. Tiles of one image are assembled into one picture. If the recording contains several images, they are saved as `OUTPUT_1.png`, `OUTPUT_2.png`, …

Exit codes:

| Code | Meaning |
|-----:|---------|
| 0 | all images received completely |
| 1 | error (file not found or unreadable, no transmissions found) |
| 2 | invalid command-line arguments |
| 3 | images decoded, but at least one transmission has no end-of-transmission signal |

## Python API

```python
from PIL import Image
import manulx

audio, rate = manulx.encode_audio(Image.open("test_card.png"), mode="yuv", pt_us=275,
                                  marker=64, tile=None, rate=11025)
manulx.write_wav("signal.wav", audio, rate)

samples, rate = manulx.load_audio("signal.wav")
for result in manulx.decode_samples(samples, rate):
    result["image"].save("decoded.png")
```

| Function | Description |
|----------|-------------|
| `encode_audio(img, mode, pt_us, marker, tile, max_side, call, image_id, rate, log, progress, stream, stop_after)` | PIL image → `(samples float32, sample_rate)` |
| `write_wav(path, samples, rate)` | write 16-bit mono WAV |
| `load_audio(path)` | read a recording → `(samples, sample_rate)` |
| `read_wav(path)` | read WAV without extra packages |
| `decode_samples(samples, rate, log, progress)` | list of dicts: `image` (PIL), `id`, `w`, `h`, `tiles`, `call`, `complete`, `notes` |
| `plan(W, H, tile, mode, pt_us, marker, call, image_id, stream)` | tile headers and total duration in seconds, without encoding |
| `eot_watch(samples, rate)` | for live recording: time of the last end-of-transmission signal and whether a new transmission started after it |
| `ManulXError` | exception for invalid parameters and undecodable input |

## Examples

The [examples](examples) directory contains source images, encoded signals (11025 Hz) and the results of decoding them.

| Source | Command | Signal | Duration | Decoded |
|--------|---------|--------|---------:|---------|
| [test_card.png](examples/test_card.png) | `manulx encode test_card.png test_card_yuv.wav --rate 11025` | [test_card_yuv.wav](examples/test_card_yuv.wav) | 42.7 s | [test_card_yuv_decoded.png](examples/test_card_yuv_decoded.png) |
| [test_card.png](examples/test_card.png) | `manulx encode test_card.png test_card_rgb.wav --mode rgb --rate 11025` | [test_card_rgb.wav](examples/test_card_rgb.wav) | 77.1 s | [test_card_rgb_decoded.png](examples/test_card_rgb_decoded.png) |
| [test_card.png](examples/test_card.png) | `manulx encode test_card.png test_card_gray.wav --mode gray --rate 11025` | [test_card_gray.wav](examples/test_card_gray.wav) | 31.0 s | [test_card_gray_decoded.png](examples/test_card_gray_decoded.png) |
| [test_card.png](examples/test_card.png) | `manulx encode test_card.png test_card_stream.wav --stream --rate 11025` | [test_card_stream.wav](examples/test_card_stream.wav) | 42.7 s | [test_card_stream_decoded.png](examples/test_card_stream_decoded.png) |
| [tiles_source.png](examples/tiles_source.png) | `manulx encode tiles_source.png tiles.wav --tile 160x100 --rate 11025` | [tiles.wav](examples/tiles.wav) | 51.5 s | [tiles_decoded.png](examples/tiles_decoded.png) |

[examples/encode_decode.py](examples/encode_decode.py) is the Python API example.

## License

MIT, see [LICENSE](LICENSE).
