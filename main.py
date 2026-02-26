import subprocess
import sys
from pathlib import Path

import cv2
import freetype
import numba
import numpy as np
from tqdm import tqdm


class FastTimerVideoGenerator:
    FORMATS = {
        "hms": ("{h:0>2d}:{m:0>2d}:{s:0>2d}", "00:00:00"),
        "hms.ms": ("{h:0>2d}:{m:0>2d}:{s:0>2d}.{ms:0>2d}", "00:00:00.00"),
        "ms": ("{m:0>2d}:{s:0>2d}", "00:00"),
        "ms.ms": ("{m:0>2d}:{s:0>2d}.{ms:0>2d}", "00:00.00"),
    }

    def __init__(self, font_path, output_path="output.mkv",
                 width: int = 1920, height: int = 1080, fps: int = 30, encoder="libx265",
                 total_seconds: int = 80 * 60 * 60, acceleration: int = 120, fmt: str = "hms"):
        """
        初始化视频生成器

        参数:
            font_path: 字体文件路径
            output_path: 输出视频路径
        """
        self.font_path = Path(font_path)
        self.output_path = Path(output_path)

        # 视频参数
        self.width = width
        self.height = height
        self.fps = fps
        self.encoder = encoder
        self.format, self.format_tmp = self.FORMATS[fmt]

        # 时间参数
        self.acceleration = acceleration  # 加速倍率
        self.total_seconds = total_seconds  # 计时器总时长
        self.video_seconds = self.total_seconds / self.acceleration  # 视频时长
        self.total_frames = int(self.video_seconds * self.fps)  # 视频总帧数

        # 预分配内存
        self.buffer_template = np.zeros((self.height, self.width, 3), dtype=np.uint8)  # 背景模板
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

    def init_font(self):
        """初始化字体并自动计算合适的大小"""
        self.face = freetype.Face(str(self.font_path))

        # 动态计算字体大小以适应屏幕
        test_size = 100
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

    def calc_text_bbox_for_size(self, text):
        """计算文本边界框"""
        width = 0
        max_ascent = 0
        max_descent = 0

        for char in text:
            self.face.load_char(char, freetype.FT_LOAD_RENDER | freetype.FT_LOAD_NO_BITMAP)
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

    @staticmethod
    def process_bitmap(bitmap):
        """处理位图数据，不使用Numba"""
        rows = bitmap.rows
        width = bitmap.width
        pitch = bitmap.pitch
        buffer = bitmap.buffer

        if not buffer:
            return np.zeros((rows, width), dtype=np.uint8)

        # 创建输出数组
        output = np.zeros((rows, width), dtype=np.uint8)

        # 处理位图数据
        for i in range(rows):
            row_offset = i * pitch
            for j in range(width):
                byte_idx = j // 8
                bit_idx = 7 - (j % 8)  # freetype使用MSB
                if byte_idx < len(buffer) and (buffer[row_offset + byte_idx] >> bit_idx) & 1:
                    output[i, j] = 255

        return output

    def get_char_bitmap(self, char):
        """获取字符位图（带缓存）"""
        if char not in self.char_cache:
            self.face.load_char(char, freetype.FT_LOAD_RENDER | freetype.FT_LOAD_TARGET_MONO)
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
    def render_to_buffer_fast(frame_buffer: np.ndarray, bitmap: np.ndarray,
                              y_start, y_end, x_start, x_end,
                              by1, by2, bx1, bx2):
        """使用Numba加速渲染到缓冲区"""
        for i in range(by1, by2):
            for j in range(bx1, bx2):
                if bitmap[i, j] > 0:
                    k = bitmap[i, j]
                    # 设置BGR三个通道为白色
                    y = y_start + (i - by1)
                    x = x_start + (j - bx1)
                    frame_buffer[y, x, 0] = k
                    frame_buffer[y, x, 1] = k
                    frame_buffer[y, x, 2] = k

    @staticmethod
    def render_to_buffer_fast_numpy(frame_buffer: np.ndarray, bitmap: np.ndarray,
                                    y_start, y_end, x_start, x_end,
                                    by1, by2, bx1, bx2):
        """使用numpy进行字符绘制"""
        # 提取有效区域
        valid_bitmap = bitmap[by1:by2, bx1:bx2]

        # 确保目标区域不越界
        y_len = min(y_end - y_start, valid_bitmap.shape[0])
        x_len = min(x_end - x_start, valid_bitmap.shape[1])

        # 向量化复制
        frame_buffer[y_start:y_start + y_len, x_start:x_start + x_len, :] = \
            np.stack([valid_bitmap[:y_len, :x_len]] * 3, axis=-1)

    def format_time(self, total_seconds: float):
        """格式化时间为HH:MM:SS 或者 HH:MM:SS.MS"""
        hours = int(total_seconds // 3600)
        minutes = int((total_seconds % 3600) // 60)
        seconds = int(total_seconds % 60)
        milliseconds = int((total_seconds % 1) * 100)
        return self.format.format(h=hours, m=minutes, s=seconds, ms=milliseconds)

    def render_text_fast(self, text):
        """快速渲染文本到预分配缓冲区"""
        # 清空文字区域
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

                    # 使用Numba加速渲染
                    self.render_to_buffer_fast(
                        self.frame_buffer, bitmap,
                        y1, y2, x1, x2,
                        by1, by2, bx1, bx2
                    )

            x_offset += char_data['advance']

    def generate_video_ffmpeg(self):
        """使用FFmpeg管道生成视频（更高效）"""
        # 计算FFmpeg命令
        ffmpeg_cmd = [
            'ffmpeg',
            '-y',  # 覆盖输出文件
            '-f', 'rawvideo',
            '-vcodec', 'rawvideo',
            '-s', f'{self.width}x{self.height}',
            '-pix_fmt', 'bgr24',  # OpenCV使用BGR格式
            '-r', str(self.fps),
            '-i', '-',  # 从标准输入读取
            '-c:v', 'hevc_nvenc',
            '-preset', 'p3',  # 最快的编码预设
            '-crf', '18',  # 高质量
            '-pix_fmt', 'yuv420p',
            str(self.output_path)
        ]

        print(f"生成视频: {self.total_frames}帧 ({self.video_seconds:.1f}秒)")
        print(f"实际时间: {self.total_seconds}秒, 加速: {self.acceleration}倍")

        # 启动FFmpeg进程
        proc = subprocess.Popen(ffmpeg_cmd, stdin=subprocess.PIPE,
                                stderr=subprocess.DEVNULL, stdout=subprocess.DEVNULL)

        try:
            # 生成每一帧
            for frame_idx in tqdm(range(self.total_frames), desc="生成视频"):
                # 计算当前时间
                video_time = frame_idx / self.fps
                real_time = video_time * self.acceleration

                # 格式化时间
                time_str = self.format_time(real_time)

                # 渲染文字
                self.render_text_fast(time_str)

                # 写入帧到FFmpeg
                proc.stdin.write(self.frame_buffer.tobytes())

            proc.stdin.close()
            proc.wait()

            print(f"视频已保存到: {self.output_path}")

        except Exception as e:
            print(f"生成视频时出错: {e}")
            proc.terminate()
            raise

    def generate_video_opencv(self):
        """使用OpenCV生成视频（备用方案）"""
        # 创建视频写入器
        fourcc = cv2.VideoWriter_fourcc(*'mp4v')
        out = cv2.VideoWriter(
            str(self.output_path),
            fourcc,
            self.fps,
            (self.width, self.height)
        )

        if not out.isOpened():
            print("警告: 无法使用HEVC编码，尝试使用H.264")
            fourcc = cv2.VideoWriter_fourcc(*'avc1')
            out = cv2.VideoWriter(
                str(self.output_path),
                fourcc,
                self.fps,
                (self.width, self.height)
            )

        print(f"生成视频: {self.total_frames}帧 ({self.video_seconds:.1f}秒)")
        print(f"实际时间: {self.total_seconds}秒, 加速: {self.acceleration}倍")

        try:
            for frame_idx in tqdm(range(self.total_frames), desc="生成视频"):
                # 计算当前时间
                video_time = frame_idx / self.fps
                real_time = video_time * self.acceleration

                # 格式化时间
                time_str = self.format_time(real_time)

                # 渲染文字
                self.render_text_fast(time_str)

                # 写入帧
                out.write(self.frame_buffer)

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
    parser.add_argument('font_path', help='字体文件路径', default='MapleMono-NF-CN-Bold.ttf')
    parser.add_argument('-o', '--output', default='timer_output.mkv',
                        help='输出视频路径')
    parser.add_argument('-d', '--duration', type=int, default=80 * 60 * 60,
                        help='计时器时长 (秒) (默认: 80小时)')
    parser.add_argument('-a', "--acceleration", type=int, default=120,
                        help='加速倍数 (默认: 120)')
    parser.add_argument('-fps', type=int, default=30,
                        help='帧率 (默认: 30)')
    parser.add_argument('-enc', "--encoder", type=str, default="libx265",
                        help='编码器 (默认: libx265) (N卡用: hevc_nvenc) (A卡用：hevc_amf) (Intel用: hevc_qsv)')
    parser.add_argument('--width', type=int, default=1920,
                        help='视频宽度 (默认: 1920)')
    parser.add_argument('--height', type=int, default=1080,
                        help='视频高度 (默认: 1080)')
    parser.add_argument('-f', "--format", type=str, default="hms",
                        help='支持hms(00:00:00), hms.ms(00:00:00.00), ms(00:00), ms.ms(00:00.00)')
    parser.add_argument('--no-ffmpeg', action='store_true',
                        help='不使用FFmpeg（使用OpenCV）')
    parser.add_argument('--no-numba', action='store_true',
                        help='不使用Numba加速')

    args = parser.parse_args()

    # 检查字体文件
    if not Path(args.font_path).exists():
        print(f"错误: 字体文件 '{args.font_path}' 不存在")
        sys.exit(1)

    # 创建生成器
    generator = FastTimerVideoGenerator(args.font_path, args.output,
                                        args.width, args.height, args.fps, args.encoder,
                                        args.duration, args.acceleration, args.format)

    # 如果不使用Numba，替换渲染函数
    if args.no_numba:
        # 使用纯Python渲染函数
        def render_text_simple(self, text):
            """简单的渲染文本函数，不使用Numba"""
            # 清空文字区域
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

                        # 直接渲染
                        for i in range(by1, by2):
                            for j in range(bx1, bx2):
                                if bitmap[i, j] > 0:
                                    y = y1 + (i - by1)
                                    x = x1 + (j - bx1)
                                    self.frame_buffer[y, x] = [255, 255, 255]

                x_offset += char_data['advance']

        # 替换渲染函数
        generator.render_text_fast = lambda text: render_text_simple(generator, text)

    # 生成视频
    try:
        generator.generate(use_ffmpeg=not args.no_ffmpeg)
    except KeyboardInterrupt:
        print("\n用户中断")
        sys.exit(1)
    except Exception as e:
        print(f"错误: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
