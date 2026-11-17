"""Command-line interface: `manulx encode` / `manulx decode`."""

import argparse
import sys

from PIL import Image

from . import __version__
from .core import ManulXError, decode_samples, encode_audio, load_audio, write_wav


def parse_tile(s):
    try:
        w, h = s.lower().split("x")
        return int(w), int(h)
    except ValueError:
        raise argparse.ArgumentTypeError("expected WxH, e.g. 320x240")


def cmd_encode(a):
    audio, rate = encode_audio(
        Image.open(a.input), mode=a.mode, pt_us=a.pt, marker=a.marker, tile=a.tile,
        max_side=a.max_side, call=a.call, image_id=a.image_id, rate=a.rate,
        stream=a.stream, stop_after=a.stop_after,
        log=(lambda s: None) if a.quiet else print,
    )
    write_wav(a.output, audio, rate)
    if not a.quiet:
        print(f"-> {a.output}")


def cmd_decode(a):
    x, rate = load_audio(a.input)
    results = decode_samples(x, rate, log=(lambda s: None) if a.quiet else print)
    incomplete = False
    for i, r in enumerate(results):
        out = a.output
        if len(results) > 1:
            stem, dot, ext = a.output.rpartition(".")
            out = f"{stem}_{i + 1}.{ext}" if dot else f"{a.output}_{i + 1}"
        r["image"].save(out)
        incomplete |= not r["complete"]
        print(f"image #{r['id']} {r['w']}x{r['h']}, tiles {r['tiles']}, "
              f"{'complete' if r['complete'] else 'INCOMPLETE'} -> {out}")
    return 3 if incomplete else 0


def build_parser():
    p = argparse.ArgumentParser(prog="manulx", description="ManulX: send images over audio.")
    p.add_argument("--version", action="version", version=f"manulx {__version__}")
    sp = p.add_subparsers(dest="cmd", required=True)

    e = sp.add_parser("encode", help="image -> WAV")
    e.add_argument("input", help="input image (PNG, JPEG, ...)")
    e.add_argument("output", help="output WAV file")
    e.add_argument("--mode", choices=["yuv", "rgb", "gray"], default="yuv", help="color mode (default: yuv)")
    e.add_argument("--pt", type=float, default=275, help="time per sample, us, 5..1280 (default: 275)")
    e.add_argument("--marker", type=int, default=64, help="in-line marker every N samples, 0 = none (default: 64)")
    e.add_argument("--tile", type=parse_tile, help="split into tiles WxH, e.g. 320x240")
    e.add_argument("--max-side", type=int, default=0, help="downscale so that the longer side <= N")
    e.add_argument("--call", default="", help="callsign, up to 6 characters")
    e.add_argument("--image-id", type=int, default=1, help="image id 0..255 (default: 1)")
    e.add_argument("--rate", type=int, default=48000, help="sample rate, Hz (default: 48000)")
    e.add_argument("--stream", action="store_true", help="stream mode: height is not sent, end is marked by EOT")
    e.add_argument("--stop-after", type=int, help="stop each tile after N lines (early stop, announced by EOT)")
    e.add_argument("-q", "--quiet", action="store_true", help="no progress output")
    e.set_defaults(func=cmd_encode)

    d = sp.add_parser("decode", help="audio -> PNG")
    d.add_argument("input", help="recording: WAV; OGG/Opus, FLAC, MP3 via soundfile or ffmpeg")
    d.add_argument("output", help="output PNG (several images get suffixes _1, _2, ...)")
    d.add_argument("-q", "--quiet", action="store_true", help="print only the result")
    d.set_defaults(func=cmd_decode)
    return p


def main(argv=None):
    a = build_parser().parse_args(argv)
    try:
        code = a.func(a)
    except ManulXError as e:
        print(f"manulx: {e}", file=sys.stderr)
        sys.exit(1)
    except FileNotFoundError as e:
        print(f"manulx: {e}", file=sys.stderr)
        sys.exit(1)
    sys.exit(code or 0)


if __name__ == "__main__":
    main()
