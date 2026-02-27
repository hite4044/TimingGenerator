# TimingGenerator

计时视频生成器

```
usage: main.py [-h] [-o OUTPUT]
               [-off OFFSET] [-d DURATION] [-a ACCELERATION]
               [-f FORMAT] [-fg FONT_COLOR] [-bg BACKGROUND_COLOR]
               [-fps FPS] [-enc ENCODER] [-crf CRF] [-preset PRESET] [-b BITRATE]
               [--width WIDTH] [--height HEIGHT]
               [--no-ffmpeg] [--no-numpy] [--no-numba]
               font_path

生成正计时视频

positional arguments:
  font_path             字体文件路径, 推荐为等宽字体

options:
  -h, --help            show this help message and exit
  -o OUTPUT, --output OUTPUT
                        输出视频路径

计时器设置:
  -off OFFSET, --offset OFFSET
                        计时器起始偏移 (秒) (默认: 0)
  -d DURATION, --duration DURATION
                        计时器时长 (秒) (默认: 80小时)
  -a ACCELERATION, --acceleration ACCELERATION
                        加速倍数 (默认: 120)
  -f FORMAT, --format FORMAT
                        支持hms(00:00:00), hms.ms(00:00:00.00), ms(00:00), ms.ms(00:00.00)
  -fg FONT_COLOR, --font_color FONT_COLOR
                        字体颜色 (默认: 255)
  -bg BACKGROUND_COLOR, --background_color BACKGROUND_COLOR
                        背景颜色 (默认: 0)

视频设置:
  -fps FPS              帧率 (默认: 30)
  -enc ENCODER, --encoder ENCODER
                        编码器 (默认: 自动根据显卡决定) (N卡用: hevc_nvenc) (A卡用：hevc_amf) (Intel用: hevc_qsv)
  -crf CRF              编码器使用的质量值(越低质量越好) (默认: 18)
  -preset PRESET        编码器使用的预设 (N卡: p1-p7)
  -b BITRATE, --bitrate BITRATE
                        编码器使用的码率 (kbps) (默认: 2000)
  --width WIDTH         视频宽度 (默认: 1920)
  --height HEIGHT       视频高度 (默认: 1080)

其他:
  --no-ffmpeg           不使用FFmpeg进行视频编码, 将通过OpenCV使用mp4v进行CPU编码, -crf -enc 将不可用
  --no-numpy            字符位图绘制不使用Numpy进行加速, 而使用numba加速的逐像素更改
  --no-numba            字符位图绘制使用python原生遍历算法, 性能极低, 非必要不要启用```