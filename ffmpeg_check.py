import subprocess
import sys
from os.path import exists
from subprocess import Popen, DEVNULL
from pathlib import Path

def check_ffmpeg():
    try:
        subprocess.run(['ffmpeg', '-version'], capture_output=True, check=True)
        return 'ffmpeg'
    except FileNotFoundError:
        pass
    if exists('ffmpeg.exe'):
        return 'ffmpeg.exe'
    print("没有检测到FFmpeg安装")
    print("可使用 --no-ffmpeg 选项以使用OpenCV进行编码")
    if Path('include_ffmpeg/ffmpeg.7z.001').exists():
        ret = input("本版本附带ffmpeg, 是否解压缩使用? (Y/n):")
        if ret.lower() == 'y':
            print("解压中...")
            proc = Popen(['include_ffmpeg/7z.exe', 'e', 'include_ffmpeg/ffmpeg.7z.001', '-y'], stdout=DEVNULL)
            proc.wait(timeout=10)
            if proc.returncode == 0:
                print("解压成功")
                return 'ffmpeg.exe'
            else:
                print("解压失败")
                sys.exit(1)
        else:
            print("程序退出")
            sys.exit(1)
    else:
        print("请自行安装FFmpeg")
        sys.exit(1)
