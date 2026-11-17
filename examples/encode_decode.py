"""Example: encode an image to a WAV file and decode it back using the Python API."""

from PIL import Image

import manulx

# Encode: image -> audio samples
audio, rate = manulx.encode_audio(
    Image.open("test_card.png"),
    mode="yuv",      # "yuv" | "rgb" | "gray"
    pt_us=275,       # time per sample, microseconds
    marker=64,       # in-line marker every 64 samples (0 = none)
    tile=None,       # e.g. (160, 100) to split into tiles
    rate=11025,      # sample rate of the output
)
manulx.write_wav("api_example.wav", audio, rate)

# Decode: audio file -> images
samples, rate = manulx.load_audio("api_example.wav")
for result in manulx.decode_samples(samples, rate):
    print(result["w"], result["h"], "complete" if result["complete"] else "incomplete")
    result["image"].save("api_example_decoded.png")
