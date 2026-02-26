import platform
import re
import subprocess


def get_gpu_info() -> list[str]:
    """获取系统显卡型号列表（跨平台）"""
    gpu_list = []
    system = platform.system()

    if system == "Windows":
        # Windows: 通过WMIC查询
        cmd = "wmic path win32_VideoController get name"
        output = subprocess.check_output(cmd, shell=True, text=True, encoding='utf-8')
        lines = output.strip().split('\n')
        for line in lines[1:]:  # 跳过表头
            if line.strip():
                gpu_list.append(line.strip())

    elif system == "Linux":
        # Linux: 通过lspci命令
        try:
            output = subprocess.check_output("lspci | grep -i vga", shell=True, text=True,
                                             stderr=subprocess.DEVNULL)
            for line in output.strip().split('\n'):
                if line:
                    # 提取型号信息（去掉PCI ID部分）
                    match = re.search(r':\s*(.+)$', line)
                    if match:
                        gpu_list.append(match.group(1).strip())
        except (subprocess.CalledProcessError, FileNotFoundError):
            # 备选方案：读取/proc文件系统
            try:
                with open('/proc/driver/nvidia/gpus/0/information', 'r') as f:
                    for line in f:
                        if 'Model:' in line:
                            gpu_list.append(line.split(':', 1)[1].strip())
            except (FileNotFoundError, PermissionError) as e:
                print("无法获取显卡信息:", e)

    elif system == "Darwin":  # macOS
        # macOS: 通过system_profiler命令
        cmd = "system_profiler SPDisplaysDataType | grep -E 'Chipset Model:|Device ID:'"
        output = subprocess.check_output(cmd, shell=True, text=True, stderr=subprocess.DEVNULL)
        lines = output.strip().split('\n')
        for i in range(0, len(lines), 2):
            if 'Chipset Model:' in lines[i]:
                model = lines[i].split(':', 1)[1].strip()
                gpu_list.append(model)

    return gpu_list


# 使用示例
if __name__ == "__main__":
    gpus = get_gpu_info()
    for i, gpu in enumerate(gpus, 1):
        print(f"显卡 {i}: {gpu}")
