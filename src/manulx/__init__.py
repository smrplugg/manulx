"""ManulX: modular SSTV-like protocol for sending images over audio."""

from .core import (
    ManulXError,
    decode_samples,
    encode_audio,
    eot_watch,
    load_audio,
    plan,
    read_wav,
    write_wav,
)

__version__ = "1.1.0"

__all__ = [
    "ManulXError",
    "decode_samples",
    "encode_audio",
    "eot_watch",
    "load_audio",
    "plan",
    "read_wav",
    "write_wav",
    "__version__",
]
