# ManulX protocol, version 1.1

[Русская версия](PROTOCOL.ru.md)

ManulX transmits an image as an audio signal. Image size, color mode, timing and tiling are described in the header of every transmission, so the receiver does not need to know the mode in advance.

## 1. Signal

The signal is a single sine tone with varying frequency (frequency modulation). The phase is continuous: when the frequency changes, the phase is carried over, so the signal has no discontinuities. Amplitude carries no information.

A sample value `v` (0…255) is sent as the frequency

```
f = 1500 Hz + v / 255 × 800 Hz
```

| Value | Frequency |
|------:|----------:|
| 0 (black) | 1500.0 Hz |
| 64 | 1700.8 Hz |
| 128 | 1901.6 Hz |
| 192 | 2102.4 Hz |
| 255 (white) | 2300.0 Hz |

Every sample lasts the same time `pt` (time per sample), 5…1280 µs, default 275 µs.

Service tones lie outside the image band:

| Frequency | Purpose |
|----------:|---------|
| 1900 Hz | leader, separators |
| 1200 Hz | sync pulses (header, line, end of transmission) |
| 1100 Hz | in-line markers |

Digital data (header, line numbers, end of transmission) use the image band frequencies: bit `0` = 1500 Hz, bit `1` = 2300 Hz. Bits are sent most significant first.

## 2. Transmission structure

One transmission carries one image or one tile.

| Part | Content | Duration |
|------|---------|---------:|
| Leader | 1900 Hz 300 ms, 1200 Hz 10 ms, 1900 Hz 300 ms | 610 ms |
| Header, 3 copies | each: 1900 Hz 20 ms, 1200 Hz 30 ms, 158 bits × 5 ms | 3 × 840 ms |
| Separator | 1900 Hz | 10 ms |
| Lines | lines 0 … n−1 (section 5) | n × line time |
| End of transmission, 2 copies | each: 1900 Hz 20 ms, 1200 Hz 30 ms, 32 bits × 5 ms | 2 × 210 ms |
| Tail | 1900 Hz | 50 ms |

Consecutive transmissions (tiles) are separated by 300 ms of silence.

## 3. Header (158 bits)

| Field | Bits | Meaning |
|-------|-----:|---------|
| magic | 4 | `1010` (0xA) |
| version | 4 | `1` |
| mode | 2 | 0 = grayscale, 1 = YUV 4:2:0, 2 = RGB |
| pt_code | 8 | time per sample = (pt_code + 1) × 5 µs |
| marker | 8 | in-line marker every N samples; 0 = no markers |
| tile_w | 12 | tile width |
| tile_h | 12 | tile height; 0 = stream mode |
| tile_x | 12 | tile position in the image, X |
| tile_y | 12 | tile position in the image, Y |
| full_w | 12 | image width |
| full_h | 12 | image height; 0 = stream mode |
| image_id | 8 | image number; tiles with the same id form one image |
| callsign | 36 | 6 characters × 6 bits |
| CRC | 16 | CRC-16/CCITT of all preceding bits |

CRC-16/CCITT: polynomial 0x1021, initial value 0xFFFF, no reflection, no final XOR.

Callsign: each character is its index in the alphabet `" ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789/-."` (space = 0, A = 1 … Z = 26, 0 = 27 …), written as 6 bits. The callsign is padded with spaces to 6 characters.

The header is sent three times. Each copy starts with its own 1900 → 1200 Hz edge, which the receiver uses to align that copy in time. If no copy passes the CRC check, the receiver takes the majority value of each bit across the copies and checks the CRC again.

Example: 320×240, YUV, 275 µs, marker every 64, image_id 1, callsign `R0ABC`:

```
magic     1010
version   0001
mode      01                   YUV
pt_code   00110110             54 -> (54 + 1) × 5 = 275 µs
marker    01000000             64
tile_w    000101000000         320
tile_h    000011110000         240
tile_x    000000000000         0
tile_y    000000000000         0
full_w    000101000000         320
full_h    000011110000         240
image_id  00000001             1
callsign  010010 011011 000001 000010 000011 000000
          R=18   0=27   A=1    B=2    C=3    space
CRC       0100000110110010
```

## 4. Color modes

**Grayscale (mode 0).** Each line carries luminance:

```
Y = 0.299 R + 0.587 G + 0.114 B
```

**RGB (mode 2).** Each line carries all R samples, then all G samples, then all B samples.

**YUV 4:2:0 (mode 1).**

```
Y  = 0.299 R + 0.587 G + 0.114 B
Cb = 128 − 0.168736 R − 0.331264 G + 0.5 B
Cr = 128 + 0.5 R − 0.418688 G − 0.081312 B
```

Luminance is sent in every line at full resolution. Chroma is averaged over 2×2 pixel blocks (chroma width = ⌈w / 2⌉; for an odd width the last column and for an odd height the last row are duplicated for averaging). Each pair of lines shares the chroma:

| Line | Payload |
|------|---------|
| even (0, 2, 4 …) | Y[w] + Cr[⌈w/2⌉] |
| odd (1, 3, 5 …) | Y[w] + Cb[⌈w/2⌉] |

The receiver combines Cr of the even line and Cb of the odd line and converts back to RGB.

## 5. Line

| Part | Content | Duration |
|------|---------|---------:|
| Sync | 1200 Hz | 5 ms |
| Porch | 1500 Hz | 1 ms |
| Line number | 12 bits of the number + 1 parity bit, 1 ms each | 13 ms |
| Guard | first sample of the payload | 0.3 ms |
| Payload | samples of `pt` each, with markers | see below |
| Guard | last sample of the payload | 0.3 ms |

Parity bit = sum of the 12 number bits mod 2. Example: line 5 → `000000000101`, parity `0`.

The payload is the sample sequence of the line (section 4). If `marker` > 0, after every `marker` samples (except at the end of the line) a marker is inserted:

| Part | Content | Duration |
|------|---------|---------:|
| Guard | previous sample | 0.3 ms |
| Marker | 1100 Hz | 1 ms |
| Guard | next sample | 0.3 ms |

A guard repeats the neighbouring sample value; it keeps the receiver's filter from smearing service tones into the image.

Line time:

```
T_line = 5 + 1 + 13 + 2 × 0.3 + N × pt + M × (1 + 2 × 0.3)   [ms]
N = payload length: w (grayscale), w + ⌈w/2⌉ (YUV), 3w (RGB)
M = ⌊(N − 1) / marker⌋ if marker > 0, otherwise 0
```

## 6. End of transmission (32 bits)

| Field | Bits | Meaning |
|-------|-----:|---------|
| magic | 4 | `0101` (0x5) |
| image_id | 8 | image number |
| lines | 12 | number of lines actually sent |
| CRC | 8 | CRC-8 of the preceding 24 bits |

CRC-8: polynomial 0x07, initial value 0x00, no reflection, no final XOR.

Example, image 1, 240 lines: `0101 00000001 000011110000 10101101`.

Sent twice, each copy as in section 2. The receiver interprets it as follows:

| Situation | Meaning |
|-----------|---------|
| received, lines = tile_h | the tile was received completely |
| received, lines < tile_h | the transmission was stopped early; there are no further lines |
| received, tile_h = 0 | stream mode; the number of lines is the image height |
| not received | the transmission was interrupted; lines after the last received line are missing |

## 7. Tiles and stream mode

**Tiles.** The image is split into rectangles of `tile_w × tile_h`, row by row starting at the top-left corner; edge tiles may be smaller. Each tile is a separate transmission with its own header containing `tile_x`, `tile_y`, `full_w`, `full_h`. The receiver assembles tiles with the same `image_id` and image size into one image.

**Stream mode.** `tile_h` = `full_h` = 0. Lines are sent until the end of transmission; the height comes from it. Tiling is possible only by width.

## 8. Duration

```
T_tile = 0.610 + 3 × 0.840 + 0.010 + h × T_line + 2 × 0.210 + 0.050   [s]
```

Example: 320×240, 275 µs, marker 64.

| Mode | Line | Transmission |
|------|-----:|-------------:|
| YUV 4:2:0 | 162.8 ms | 42.7 s |
| Grayscale | 114.0 ms | 31.0 s |
| RGB | 306.0 ms | 77.1 s |

YUV line: 5 + 1 + 13 + 0.6 + 480 × 0.275 + 7 × 1.6 = 162.8 ms.

## 9. Reference decoder

The decoder in this repository works as follows:

1. Resamples the audio to 24 kHz, shifts it down by 1700 Hz and low-pass filters it (passband about 200–3200 Hz).
2. Estimates the frequency from the phase rotation between neighbouring samples. Averaging is done on the complex product of neighbouring samples, not on the frequency; bits are decided by the median frequency of the bit interval.
3. Finds the header: a 1200 Hz pulse of about 30 ms preceded by the 1900 Hz leader. Reads the three copies and checks the CRC.
4. Finds line sync pulses, reads line numbers and places lines by number. A line number is accepted if it agrees with the number of a neighbouring sync pulse and the time between them. The line period is adjusted to the actual playback speed.
5. Inside a line, finds the markers and adjusts the timing of each segment between them. The mean frequency of each sample interval is converted back to a value: `v = (f − 1500) / 800 × 255`.
6. Finds the end of transmission. Lines missing inside the image are filled from neighbouring lines; missing lines at the end of an interrupted transmission are left empty.
7. Converts color back to RGB and assembles tiles.
