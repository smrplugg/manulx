# ManulX

[English version](README.md)

ManulX — модульный SSTV-подобный протокол передачи изображений через звук. Размер изображения, цветовой режим, временные параметры и разбиение на тайлы описываются в заголовке каждой передачи, а конец каждой передачи отмечается сигналом окончания.

Спецификация протокола: [PROTOCOL.ru.md](PROTOCOL.ru.md).

## Установка

```
pip install manulx
```

Из каталога с исходниками:

```
pip install .
```

Требования: Python 3.10+, numpy, scipy, pillow.

WAV-файлы читаются без дополнительных пакетов. Для декодирования OGG/Opus, FLAC, MP3 и других форматов установите дополнение `formats` (soundfile) или добавьте `ffmpeg` в `PATH`:

```
pip install "manulx[formats]"
```

## Использование

```
manulx encode image.png signal.wav
manulx decode signal.wav image.png
```

Те же команды доступны как `python -m manulx`.

### encode

```
manulx encode INPUT OUTPUT [параметры]
```

| Параметр | По умолчанию | Описание |
|----------|--------------|----------|
| `--mode {yuv,rgb,gray}` | `yuv` | цветовой режим |
| `--pt US` | `275` | время на отсчёт, мкс (5…1280) |
| `--marker N` | `64` | метка через каждые N отсчётов; 0 = без меток |
| `--tile WxH` | — | разбить на тайлы, например `320x240` |
| `--max-side N` | — | уменьшить так, чтобы большая сторона была не больше N пикселей |
| `--call CALL` | — | позывной, до 6 символов |
| `--image-id N` | `1` | номер изображения, 0…255 |
| `--rate HZ` | `48000` | частота дискретизации WAV-файла |
| `--stream` | — | потоковый режим: высота не передаётся, конец отмечается сигналом окончания |
| `--stop-after N` | — | остановить каждый тайл после N строк (досрочная остановка, сообщается сигналом окончания) |
| `-q`, `--quiet` | — | без вывода хода работы |

Максимальная сторона изображения или тайла — 4095 пикселей.

### decode

```
manulx decode INPUT OUTPUT [-q]
```

`INPUT` — запись; она может содержать несколько передач. Тайлы одного изображения собираются в одну картинку. Если в записи несколько изображений, они сохраняются как `OUTPUT_1.png`, `OUTPUT_2.png`, …

Коды завершения:

| Код | Значение |
|----:|----------|
| 0 | все изображения приняты полностью |
| 1 | ошибка (файл не найден или не читается, передачи не найдены) |
| 2 | неверные аргументы командной строки |
| 3 | изображения декодированы, но хотя бы у одной передачи нет сигнала окончания |

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

| Функция | Описание |
|---------|----------|
| `encode_audio(img, mode, pt_us, marker, tile, max_side, call, image_id, rate, log, progress, stream, stop_after)` | изображение PIL → `(отсчёты float32, частота_дискретизации)` |
| `write_wav(path, samples, rate)` | запись 16-битного моно WAV |
| `load_audio(path)` | чтение записи → `(отсчёты, частота_дискретизации)` |
| `read_wav(path)` | чтение WAV без дополнительных пакетов |
| `decode_samples(samples, rate, log, progress)` | список словарей: `image` (PIL), `id`, `w`, `h`, `tiles`, `call`, `complete`, `notes` |
| `plan(W, H, tile, mode, pt_us, marker, call, image_id, stream)` | заголовки тайлов и общая длительность в секундах, без кодирования |
| `eot_watch(samples, rate)` | для записи в реальном времени: время последнего сигнала окончания и началась ли после него новая передача |
| `ManulXError` | исключение при неверных параметрах и недекодируемом входе |

## Примеры

В каталоге [examples](examples) — исходные изображения, закодированные сигналы (11025 Гц) и результаты их декодирования.

| Исходник | Команда | Сигнал | Длительность | Результат |
|----------|---------|--------|-------------:|-----------|
| [test_card.png](examples/test_card.png) | `manulx encode test_card.png test_card_yuv.wav --rate 11025` | [test_card_yuv.wav](examples/test_card_yuv.wav) | 42,7 с | [test_card_yuv_decoded.png](examples/test_card_yuv_decoded.png) |
| [test_card.png](examples/test_card.png) | `manulx encode test_card.png test_card_rgb.wav --mode rgb --rate 11025` | [test_card_rgb.wav](examples/test_card_rgb.wav) | 77,1 с | [test_card_rgb_decoded.png](examples/test_card_rgb_decoded.png) |
| [test_card.png](examples/test_card.png) | `manulx encode test_card.png test_card_gray.wav --mode gray --rate 11025` | [test_card_gray.wav](examples/test_card_gray.wav) | 31,0 с | [test_card_gray_decoded.png](examples/test_card_gray_decoded.png) |
| [test_card.png](examples/test_card.png) | `manulx encode test_card.png test_card_stream.wav --stream --rate 11025` | [test_card_stream.wav](examples/test_card_stream.wav) | 42,7 с | [test_card_stream_decoded.png](examples/test_card_stream_decoded.png) |
| [tiles_source.png](examples/tiles_source.png) | `manulx encode tiles_source.png tiles.wav --tile 160x100 --rate 11025` | [tiles.wav](examples/tiles.wav) | 51,5 с | [tiles_decoded.png](examples/tiles_decoded.png) |

[examples/encode_decode.py](examples/encode_decode.py) — пример использования Python API.

## Лицензия

MIT, см. [LICENSE](LICENSE).
