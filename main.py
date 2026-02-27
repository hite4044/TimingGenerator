import os
import sys
from os import makedirs

t = os.path.dirname(os.path.abspath(__file__))
os.chdir(t)  # 进入当前目录
sys.path.append(t)  # 添加模块导入路径

import subprocess
import sys
from pathlib import Path

import freetype

try:
    import numba
except ImportError:
    print("Numba加速不可用")


    class FakeNumba:
        @staticmethod
        def njit(*args, **kwargs):
            def wrapper(func):
                return func

            return wrapper


    numba = FakeNumba()
import numpy as np
from tqdm import tqdm

from ffmpeg_check import check_ffmpeg
from sys_info import get_gpu_info


class FastTimerVideoGenerator:
    FORMATS = {
        "hms": ("{h:0>2d}:{m:0>2d}:{s:0>2d}", "00:00:00"),
        "hms.ms": ("{h:0>2d}:{m:0>2d}:{s:0>2d}.{ms:0>2d}", "00:00:00.00"),
        "ms": ("{m:0>2d}:{s:0>2d}", "00:00"),
        "ms.ms": ("{m:0>2d}:{s:0>2d}.{ms:0>2d}", "00:00.00"),
    }

    def __init__(self, font_path, output_path="output.mkv",
                 start_offset: float = 0, total_seconds: float = 80 * 60 * 60, acceleration: float = 120,
                 fmt: str = "hms", fg: int = 255, bg: int = 0,

                 fps: int = 30, encoder="libx265", preset: str = None, bitrate: str | None = None,
                 width: int = 1920, height: int = 1080,

                 use_numpy: bool = True, no_numba: bool = False, no_lossless: bool = False, ):
        """
        初始化视频生成器
        """
        self.font_path = Path(font_path)
        self.output_path = Path(output_path)

        # 计时器参数
        self.start_offset = start_offset  # 起始偏移
        self.acceleration = acceleration  # 加速倍率
        self.total_seconds = total_seconds  # 计时器总时长
        self.format, self.format_tmp = self.FORMATS[fmt]  # 格式
        self.video_seconds = self.total_seconds / self.acceleration  # 视频时长
        self.total_frames = int(self.video_seconds * fps)  # 视频总帧数
        self.fg = fg
        self.bg = bg

        # 视频参数
        self.fps = fps
        self.preset = preset
        self.bitrate = bitrate
        self.encoder = encoder
        self.width = width
        self.height = height
        self.no_lossless = no_lossless

        # 预分配内存
        self.buffer_template = np.zeros((self.height, self.width), dtype=np.uint8)  # 背景模板
        self.buffer_template.fill(self.bg)
        self.frame_buffer = self.buffer_template.copy()  # 用于写入的帧缓冲区

        # 初始化字体
        self.face: freetype.Face | None = None
        self.font_size = 100
        self.init_font()

        # 计算文字位置
        self.text_width, self.text_height = self.calc_text_bbox()
        self.text_x = (self.width - self.text_width) // 2
        self.text_y = (self.height + self.text_height) // 2 - int(self.text_height * 0.2)

        # 创建字形缓存
        self.char_cache = {}
        if use_numpy:  # Numpy数组操作
            self.text_render_func = self.render_char_to_buffer_numpy
        else:
            if no_numba:  # Python原生方法
                self.text_render_func = self.render_char_to_buffer_raw
            else:  # Numba加速
                self.text_render_func = self.render_char_to_buffer

        self.ffmpeg_path: str = 'ffmpeg'
        self.pre_render_chars()

    def init_font(self):
        """初始化字体并自动计算合适的大小"""
        self.face = freetype.Face(str(self.font_path))

        # 动态计算字体大小以适应屏幕
        max_size = 300
        min_size = 20

        for size in range(max_size, min_size, -10):
            self.face.set_char_size(size * 64)

            # 测试字符串渲染
            test_text = self.format_tmp
            bbox = self.calc_text_bbox_for_size(test_text)
            if bbox[2] < self.width * 0.9 and bbox[3] < self.height * 0.9:
                self.font_size = size
                print(f"使用字体大小: {size}")
                break
        else:
            self.font_size = 100

        self.face.set_char_size(self.font_size * 64)

    def pre_render_chars(self):
        """预渲染所有字符"""
        for char in "0123456789:.":
            self.get_char_bitmap(char)

    def calc_text_bbox_for_size(self, text):
        """计算文本边界框"""
        width = 0
        max_ascent = 0
        max_descent = 0

        for char in text:
            self.face.load_char(char, getattr(freetype, "FT_LOAD_RENDER"))
            glyph = self.face.glyph
            width += glyph.advance.x >> 6

            # 获取字形度量
            metrics = glyph.metrics
            ascent = metrics.horiBearingY >> 6
            descent = (metrics.height - metrics.horiBearingY) >> 6

            max_ascent = max(max_ascent, ascent)
            max_descent = max(max_descent, descent)

        height = max_ascent + max_descent
        return 0, 0, width, height

    def calc_text_bbox(self):
        """计算最终文本边界框"""
        bbox = self.calc_text_bbox_for_size(self.format_tmp)
        return bbox[2], bbox[3]

    def process_bitmap(self, bitmap):
        """处理字符灰度位图数据"""
        rows = bitmap.rows
        width = bitmap.width
        pitch = bitmap.pitch
        buffer = bitmap.buffer

        if not buffer:
            return np.zeros((rows, width), dtype=np.uint8)

        # 创建输出数组
        output = np.zeros((rows, width), dtype=np.uint8)

        # 处理位图数据（灰度模式）
        for i in range(rows):
            row_offset = i * pitch
            for j in range(width):
                # 直接读取灰度值（0~255）
                gray_value = buffer[row_offset + j]
                # 根据背景色和前景色进行线性映射
                output[i, j] = int(self.bg + (self.fg - self.bg) * (gray_value / 255))

        return output

    def get_char_bitmap(self, char):
        """获取字符位图（带缓存）"""
        if char not in self.char_cache:
            self.face.load_char(char, getattr(freetype, "FT_LOAD_RENDER"))
            glyph = self.face.glyph
            bitmap = glyph.bitmap

            # 转换位图格式
            char_img = self.process_bitmap(bitmap)

            # 缓存字符
            self.char_cache[char] = {
                'image': char_img,
                'advance': glyph.advance.x >> 6,
                'left': glyph.bitmap_left,
                'top': glyph.bitmap_top
            }

        return self.char_cache[char]

    @staticmethod
    @numba.njit(cache=True)
    def render_char_to_buffer(frame_buffer: np.ndarray, char_bitmap: np.ndarray,
                              y_start, _, x_start, __,
                              by1, by2, bx1, bx2):
        """以指定的位置绘制字符位图, Numba加速版本"""
        for i in range(by1, by2):
            for j in range(bx1, bx2):
                k = char_bitmap[i, j]
                if k > 0:
                    # 设置BGR三个通道为白色
                    y = y_start + (i - by1)
                    x = x_start + (j - bx1)
                    frame_buffer[y, x] = k

    @staticmethod
    def render_char_to_buffer_raw(frame_buffer: np.ndarray, char_bitmap: np.ndarray,
                                  y_start, _, x_start, __,
                                  by1, by2, bx1, bx2):
        """
        以指定的位置绘制字符位图
        bro逗我雷霆呢, 你用这玩意
        """
        for i in range(by1, by2):
            for j in range(bx1, bx2):
                k = char_bitmap[i, j]
                if k > 0:
                    y = y_start + (i - by1)
                    x = x_start + (j - bx1)
                    frame_buffer[y, x] = k

    @staticmethod
    def render_char_to_buffer_numpy(frame_buffer: np.ndarray, char_bitmap: np.ndarray,
                                    y_start, y_end, x_start, x_end,
                                    by1, by2, bx1, bx2):
        """以指定的位置绘制字符位图, numpy实现版本"""

        # 提取字符位图的有效部分
        char_region = char_bitmap[by1:by2, bx1:bx2]

        # 将字符位图的像素值复制到帧缓冲区的目标区域
        frame_buffer[y_start:y_end, x_start:x_end] = char_region

    def format_time(self, total_seconds: float):
        """以指定的格式格式化时间"""
        hours = int(total_seconds // 3600)
        minutes = int((total_seconds % 3600) // 60)
        seconds = int(total_seconds % 60)
        milliseconds = int((total_seconds % 1) * 100)
        return self.format.format(h=hours, m=minutes, s=seconds, ms=milliseconds)

    def render_text_to_buffer(self, text: str):
        """渲染文本到预分配缓冲区"""
        # 准备黑色背景
        self.frame_buffer = self.buffer_template.copy()

        x_offset = 0

        for char in text:
            char_data = self.get_char_bitmap(char)
            bitmap = char_data['image']
            left = char_data['left']
            top = char_data['top']

            if bitmap.size > 0:
                h, w = bitmap.shape

                # 计算目标位置
                y_start = self.text_y - top
                y_end = y_start + h
                x_start = self.text_x + x_offset + left
                x_end = x_start + w

                # 确保不越界
                if (y_start < self.height and y_end > 0 and
                        x_start < self.width and x_end > 0):
                    # 计算裁剪边界
                    y1 = max(0, y_start)
                    y2 = min(self.height, y_end)
                    x1 = max(0, x_start)
                    x2 = min(self.width, x_end)

                    # 计算位图裁剪
                    by1 = y1 - y_start
                    by2 = by1 + (y2 - y1)
                    bx1 = x1 - x_start
                    bx2 = bx1 + (x2 - x1)

                    # 渲染计时器文本
                    self.text_render_func(
                        self.frame_buffer, bitmap,
                        y1, y2, x1, x2,
                        by1, by2, bx1, bx2
                    )

            x_offset += char_data['advance']

    def generate_video_ffmpeg(self):
        """使用FFmpeg管道生成视频（更高效）"""
        # 计算FFmpeg命令
        ffmpeg_cmd = [
            f'{self.ffmpeg_path}',
            '-y',  # 覆盖输出文件
            '-f', 'rawvideo',
            '-vcodec', 'rawvideo',
            '-s', f'{self.width}x{self.height}',
            '-pix_fmt', 'gray',  # 使用灰度格式
            '-r', str(self.fps),
            '-i', '-',  # 从标准输入读取
            '-c:v', self.encoder,
        ]
        if self.preset is not None:
            ffmpeg_cmd.extend(['-preset', str(self.preset)])
        if not self.no_lossless:
            ffmpeg_cmd.extend(['-tune', 'lossless'])
        if self.bitrate is not None:
            ffmpeg_cmd.extend(['-b:v', str(self.bitrate)])
        ffmpeg_cmd.extend(['-crf', str(self.crf)])
        ffmpeg_cmd.extend(['-pix_fmt', 'yuv420p',])
        ffmpeg_cmd.extend([str(self.output_path)])

        print(f"生成视频: {self.total_frames}帧 ({self.video_seconds:.1f}秒)")
        print(f"实际时间: {self.total_seconds}秒, 加速: {self.acceleration}倍")

        # 启动FFmpeg进程
        proc = subprocess.Popen(ffmpeg_cmd, stdin=subprocess.PIPE, bufsize=1024 * 1024,
                                stderr=subprocess.STDOUT, stdout=subprocess.PIPE)

        last_text: str = ""
        try:
            # 生成每一帧
            for frame_idx in tqdm(range(self.total_frames), desc="生成视频"):
                # 计算当前时间
                video_time = frame_idx / self.fps
                real_time = video_time * self.acceleration + self.start_offset

                # 格式化时间
                time_str = self.format_time(real_time)

                if time_str != last_text:  # 重复使用相同帧
                    # 渲染文字
                    self.render_text_to_buffer(time_str)
                    last_text = time_str

                # 写入帧到FFmpeg
                proc.stdin.write(self.frame_buffer.data)

            proc.stdin.close()
            proc.stdout.read()  # 读取所有输出
            proc.wait()

            print(f"视频已保存到: {self.output_path}")

        except Exception as e:
            print(f"生成视频时出错: {e}")
            print(f"FFmpeg输出: ")
            print(proc.stdout.read().decode('utf-8'))
            proc.terminate()
            raise

    def generate_video_opencv(self):
        """使用OpenCV生成视频（备用方案）"""
        try:
            import cv2
        except ImportError:
            print("OpenCV未安装, 退出程序")
            raise
        # 创建视频写入器
        fourcc = getattr(cv2, "VideoWriter_fourcc")(*'mp4v')
        out = cv2.VideoWriter(
            str(self.output_path),
            fourcc,
            self.fps,
            (self.width, self.height)
        )

        if not out.isOpened():
            print("mp4v编码器打开失败, 退出程序")
            out.release()
            raise

        print(f"生成视频: {self.total_frames}帧 ({self.video_seconds:.1f}秒)")
        print(f"实际时间: {self.total_seconds}秒, 加速: {self.acceleration}倍")

        try:
            for frame_idx in tqdm(range(self.total_frames), desc="生成视频"):
                # 计算当前时间
                video_time = frame_idx / self.fps
                real_time = video_time * self.acceleration + self.start_offset

                # 格式化时间
                time_str = self.format_time(real_time)

                # 渲染文字
                self.render_text_to_buffer(time_str)

                # 写入帧
                out.write(cv2.cvtColor(self.frame_buffer, cv2.COLOR_GRAY2BGR))  # 需要BGR格式

            out.release()
            print(f"视频已保存到: {self.output_path}")

        except Exception as e:
            print(f"生成视频时出错: {e}")
            out.release()
            raise

    def generate(self, use_ffmpeg=True):
        """生成视频"""
        if use_ffmpeg:
            # 检查FFmpeg是否可用
            try:
                subprocess.run(['ffmpeg', '-version'], capture_output=True, check=True)
                self.generate_video_ffmpeg()
            except (subprocess.CalledProcessError, FileNotFoundError):
                print("FFmpeg不可用，使用OpenCV")
                self.generate_video_opencv()
        else:
            self.generate_video_opencv()


def main():
    """主函数"""
    import argparse

    parser = argparse.ArgumentParser(description='生成正计时视频')
    parser.add_argument('-font', '--font-path', default='MapleMono-NF-CN-Bold.ttf',
                        help='字体文件路径, 推荐为等宽字体')
    parser.add_argument('-o', '--output', default='output\\timer_output.mkv',
                        help='输出视频路径')

    # 计时器相关
    group = parser.add_argument_group("计时器设置")
    group.add_argument('-off', '--offset', type=float, default=0,
                       help='计时器起始偏移 (秒) (默认: 0)')
    group.add_argument('-d', '--duration', type=float, default=80 * 60 * 60,
                       help='计时器时长 (秒) (默认: 80小时)')
    group.add_argument('-a', "--acceleration", type=float, default=120,
                       help='加速倍数 (默认: 120)')
    group.add_argument('-f', "--format", type=str, default="hms",
                       help='支持 hms(00:00:00), hms.ms(00:00:00.00), ms(00:00), ms.ms(00:00.00)')
    group.add_argument('-fg', "--font_color", type=int, default=255,
                       help='字体颜色 (默认: 255)')
    group.add_argument('-bg', "--background_color", type=int, default=0,
                       help='背景颜色 (默认: 0)')

    # 视频相关
    group = parser.add_argument_group("视频设置")
    group.add_argument('-fps', type=int, default=30,
                       help='帧率 (默认: 30)')
    group.add_argument('-enc', "--encoder", type=str, default="%AUTO%",
                       help='编码器 (默认: 自动根据显卡决定) (N卡用: hevc_nvenc) (A卡用：hevc_amf) (Intel用: hevc_qsv)')
    group.add_argument('-preset', type=str, default=None,
                       help='编码器使用的预设 (N卡: p1-p7)')
    group.add_argument('-b', "--bitrate", type=str, default=None,
                       help='编码器使用的码率 (kbps) (例: 1000k)')
    group.add_argument('--width', type=int, default=1920,
                       help='视频宽度 (默认: 1920)')
    group.add_argument('--height', type=int, default=1080,
                       help='视频高度 (默认: 1080)')

    # 杂项
    group = parser.add_argument_group("其他")
    group.add_argument('--no-lossless', action='store_true',
                       help="不使用无损编码")
    group.add_argument('--no-ffmpeg', action='store_true',
                       help='不使用FFmpeg进行视频编码, 将通过OpenCV使用mp4v进行CPU编码, -crf -enc 将不可用')
    group.add_argument('--no-numpy', action='store_true',
                       help='字符位图绘制不使用Numpy进行加速, 而使用numba加速的逐像素更改')
    group.add_argument('--no-numba', action='store_true',
                       help='字符位图绘制使用python原生遍历算法, 性能极低, 非必要不要启用')

    args = parser.parse_args()

    # 检查字体文件
    if not Path(args.font_path).exists():
        print(f"错误: 字体文件 '{args.font_path}' 不存在")
        sys.exit(1)

    # 自动决策编码器
    encoder = args.encoder
    if encoder == "%AUTO%":
        gpus = get_gpu_info()
        print("检测到的可用GPU:", gpus)
        info_text = " ".join(gpus).lower()
        if "nvidia" in info_text:
            print("自动选择编码器: NVIDIA NVENC")
            encoder = "hevc_nvenc"
        elif "amd" in info_text:
            print("自动选择编码器: AMD AMF")
            encoder = "hevc_amf"
        elif "intel" in info_text:
            print("自动选择编码器: Intel Quick Sync Video")
            encoder = "hevc_qsv"
        else:
            print("无法决定最优编码器, 使用libx265")
            encoder = "libx265"

    # 创建生成器
    if not Path(args.output).parent.exists():
        makedirs(str(Path(args.output).parent))

    generator = FastTimerVideoGenerator(args.font_path, args.output,
                                        args.offset, args.duration, args.acceleration, args.format, args.font_color,
                                        args.background_color,
                                        args.fps, encoder, args.preset, args.bitrate, args.width, args.height,
                                        not args.no_numpy, args.no_numba)

    # 生成视频
    try:
        if not args.no_ffmpeg:
            generator.ffmpeg_path = check_ffmpeg()
        generator.generate(use_ffmpeg=not args.no_ffmpeg)
    except KeyboardInterrupt:
        print("\n用户中断")
        sys.exit(1)
    except EncodingWarning as e:
        print(f"错误: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
