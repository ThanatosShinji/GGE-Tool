#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# MIT License - see LICENSE file in root directory
"""
WAS 文件格式解析器
参考: https://github.com/duguaosnowqrl/wastools (Java实现)

WAS 文件格式:
  - 文件头标志 "SP" (2 bytes)
  - imageHeaderSize (2 bytes, unsigned short)
  - spriteCount (2 bytes, unsigned short)
  - frameCount (2 bytes, unsigned short)
  - width (2 bytes, unsigned short)
  - height (2 bytes, unsigned short)
  - centerX (2 bytes, unsigned short)
  - centerY (2 bytes, unsigned short)
  - [可选] 帧延时信息 (imageHeaderSize - 12 bytes)
  - 调色板: 256 * 2 = 512 bytes (RGB565格式)
  - 帧偏移列表: spriteCount * frameCount * 4 bytes (int32)
  - 帧数据:
      - frameX (int32)
      - frameY (int32)
      - frameWidth (int32)
      - frameHeight (int32)
      - lineOffsets: frameHeight * 4 bytes (int32, 每行数据偏移)
      - 行像素数据 (压缩格式)
"""

import struct
import io
from typing import List, Tuple, Optional


# 常量
WAS_FILE_TAG = b"SP"
TCP_FILE_TAG = b"SH"
WAS_IMAGE_HEADER_SIZE = 12

# 压缩数据类型 (前2位标志)
TYPE_FLAG = 0xC0
TYPE_ALPHA = 0x00    # 00xxxxxx - Alpha像素/重复/块结束
TYPE_PIXELS = 0x40   # 01xxxxxx - 连续像素
TYPE_REPEAT = 0x80   # 10xxxxxx - 重复像素
TYPE_SKIP = 0xC0     # 11xxxxxx - 跳过像素

# Alpha子类型 (前3位)
TYPE_ALPHA_PIXEL = 0x20    # 001xxxxx - 单个Alpha像素
TYPE_ALPHA_REPEAT = 0x00   # 000xxxxx - Alpha重复/块结束


class WasFrame:
    """WAS 帧数据"""
    def __init__(self):
        self.delay: int = 1          # 帧延时
        self.width: int = 0
        self.height: int = 0
        self.x_center: int = 0       # 图像中心点X
        self.y_center: int = 0       # 图像中心点Y
        # pixels[y][x] - 每个像素为 int (低16位RGB565, 16-20位alpha)
        self.pixels: List[List[int]] = None

    def to_argb_pixels(self):
        """
        将内部像素格式转换为 ARGB numpy 数组
        返回: numpy.ndarray, shape=(H, W, 4), dtype=uint8, channels=(a, r, g, b)
        """
        if not self.pixels:
            return np.zeros((0, 0, 4), dtype=np.uint8)

        import numpy as np

        arr = np.array(self.pixels, dtype=np.uint32)
        a = ((arr >> 16) & 0x1F).astype(np.uint8) << 3
        r = ((arr >> 11) & 0x1F).astype(np.uint8) << 3
        g = ((arr >> 5) & 0x3F).astype(np.uint8) << 2
        b = (arr & 0x1F).astype(np.uint8) << 3
        return np.stack([a, r, g, b], axis=-1)

    def to_pil_image(self):
        """
        转换为 PIL Image 对象 (需要安装 Pillow)
        使用 numpy 向量化操作，避免 Python 逐像素循环
        """
        try:
            from PIL import Image
        except ImportError:
            raise ImportError("需要 Pillow 库: pip install Pillow")

        if not self.pixels:
            return Image.new("RGBA", (self.width, self.height))

        argb = self.to_argb_pixels()  # (H, W, 4)  a,r,g,b
        rgba = argb[:, :, [1, 2, 3, 0]]  # 重排为 r,g,b,a
        return Image.fromarray(rgba, "RGBA")


class WasImage:
    """WAS 图像 (包含多个精灵和帧)"""
    def __init__(self):
        self.palette: List[int] = []       # 256个RGB565颜色值
        self.sprite_count: int = 0
        self.frame_count: int = 0
        self.width: int = 0
        self.height: int = 0
        self.x_center: int = 0
        self.y_center: int = 0
        # frames[sprite_index][frame_index]
        self.frames: List[List[WasFrame]] = None

    def get_frame(self, sprite_idx: int, frame_idx: int) -> Optional[WasFrame]:
        """获取指定精灵的指定帧"""
        if self.frames and sprite_idx < len(self.frames) and frame_idx < len(self.frames[sprite_idx]):
            return self.frames[sprite_idx][frame_idx]
        return None

    def get_all_frames_flat(self) -> List[WasFrame]:
        """获取所有帧 (扁平列表)"""
        result = []
        if self.frames:
            for sprite_frames in self.frames:
                result.extend(sprite_frames)
        return result

    def __str__(self):
        return (f"WasImage(sprites={self.sprite_count}, frames={self.frame_count}, "
                f"size={self.width}x{self.height}, center=({self.x_center},{self.y_center}))")


class WasParser:
    """WAS 文件解析器"""

    def __init__(self):
        self.palette: List[int] = [0] * 256

    def _read_unsigned_short(self, data: bytes, offset: int) -> int:
        """读取2字节无符号小端整数"""
        return struct.unpack_from('<H', data, offset)[0]

    def _read_int(self, data: bytes, offset: int) -> int:
        """读取4字节有符号小端整数"""
        return struct.unpack_from('<i', data, offset)[0]

    def _read_unsigned_int(self, data: bytes, offset: int) -> int:
        """读取4字节无符号小端整数"""
        return struct.unpack_from('<I', data, offset)[0]

    def parse(self, data: bytes) -> WasImage:
        """
        解析 WAS 文件数据
        Args:
            data: WAS 文件的二进制数据
        Returns:
            WasImage 对象
        Raises:
            ValueError: 文件格式错误
        """
        if len(data) < 4:
            raise ValueError("文件太小，不是有效的WAS文件")

        # 检查文件头标志
        tag = data[0:2]
        if tag not in (WAS_FILE_TAG, TCP_FILE_TAG):
            raise ValueError(f"文件头标志错误: {tag!r} (期望 'SP' 或 'SH')")

        # 解析头部信息
        image_header_size = self._read_unsigned_short(data, 2)
        sprite_count = self._read_unsigned_short(data, 4)
        frame_count = self._read_unsigned_short(data, 6)
        width = self._read_unsigned_short(data, 8)
        height = self._read_unsigned_short(data, 10)
        center_x = self._read_unsigned_short(data, 12)
        center_y = self._read_unsigned_short(data, 14)

        # 读取帧延时信息
        delays = []
        delay_len = image_header_size - WAS_IMAGE_HEADER_SIZE
        if delay_len < 0:
            raise ValueError(f"帧延时信息错误: {delay_len}")
        for i in range(delay_len):
            delays.append(data[16 + i])

        # 读取调色板 (偏移: imageHeaderSize + 4)
        pal_offset = image_header_size + 4
        for i in range(256):
            self.palette[i] = self._read_unsigned_short(data, pal_offset + i * 2)

        # 读取帧偏移列表 (偏移: imageHeaderSize + 4 + 512)
        frame_off_table = image_header_size + 4 + 512
        frame_offsets = []
        for i in range(sprite_count):
            row = []
            for n in range(frame_count):
                off = self._read_unsigned_int(data, frame_off_table + (i * frame_count + n) * 4)
                row.append(off)
            frame_offsets.append(row)

        # 解析帧数据
        frames: List[List[WasFrame]] = []
        for i in range(sprite_count):
            sprite_frames = []
            for n in range(frame_count):
                frame = WasFrame()
                offset = frame_offsets[i][n]
                if delays and n < len(delays):
                    frame.delay = delays[n]

                if offset != 0:
                    # 非空白帧
                    abs_offset = offset + image_header_size + 4
                    frame.x_center = self._read_int(data, abs_offset)
                    frame.y_center = self._read_int(data, abs_offset + 4)
                    frame.width = self._read_int(data, abs_offset + 8)
                    frame.height = self._read_int(data, abs_offset + 12)

                    # 读取行偏移列表
                    line_offsets = []
                    for l in range(frame.height):
                        lo = self._read_unsigned_int(data, abs_offset + 16 + l * 4)
                        line_offsets.append(lo)

                    # 解析行像素数据
                    frame.pixels = self._parse_frame_data(
                        data, offset, line_offsets, frame.width, frame.height,
                        image_header_size
                    )
                else:
                    # 空白帧: 创建空像素数组
                    frame.width = width
                    frame.height = height
                    frame.pixels = [[0] * width for _ in range(height)]

                sprite_frames.append(frame)
            frames.append(sprite_frames)

        # 构建 WasImage
        was_image = WasImage()
        was_image.palette = self.palette[:]
        was_image.sprite_count = sprite_count
        was_image.frame_count = frame_count
        was_image.width = width
        was_image.height = height
        was_image.x_center = center_x
        was_image.y_center = center_y
        was_image.frames = frames

        return was_image

    def _parse_frame_data(self, data: bytes, frame_offset: int,
                          line_offsets: List[int], frame_width: int,
                          frame_height: int, image_header_size: int) -> List[List[int]]:
        """
        解析帧的压缩像素数据

        Args:
            data: 完整的WAS文件数据
            frame_offset: 帧数据在文件中的偏移 (相对 imageHeaderSize+4)
            line_offsets: 每行数据的偏移列表
            frame_width: 帧宽度
            frame_height: 帧高度
            image_header_size: WAS图像头部大小 (用于计算绝对偏移)

        Returns:
            pixels[y][x] - 像素数据
        """
        pixels = [[0] * frame_width for _ in range(frame_height)]
        # 行数据的绝对偏移 = lineOffsets[y] + frameOffset + imageHeaderSize + 4
        # 参考Java: in.seek(lineOffsets[y] + frameOffset + imageHeaderSize + 4)
        # 其中 frameOffset 是帧偏移表中的原始值 (相对于 imageHeaderSize+4)
        # lineOffsets 是相对于 (frameOffset + imageHeaderSize + 4) 的偏移
        base_offset = frame_offset + image_header_size + 4

        for y in range(frame_height):
            x = 0
            line_start = line_offsets[y] + base_offset

            while x < frame_width:
                if line_start >= len(data):
                    break

                b = data[line_start]
                line_start += 1
                block_type = b & TYPE_FLAG

                if block_type == TYPE_ALPHA:
                    # 00xxxxxx
                    if (b & TYPE_ALPHA_PIXEL) > 0:
                        # 001xxxxx - 单个Alpha像素
                        index = data[line_start]
                        line_start += 1
                        c = self.palette[index]
                        pixels[y][x] = c + ((b & 0x1F) << 16)
                        x += 1
                    elif b != 0:
                        # 000xxxxx (非0) - Alpha重复
                        count = b & 0x1F
                        alpha = data[line_start]
                        line_start += 1
                        index = data[line_start]
                        line_start += 1
                        c = self.palette[index]
                        for _ in range(count):
                            pixels[y][x] = c + ((alpha & 0x1F) << 16)
                            x += 1
                    else:
                        # 0x00 - 块结束标志
                        if x > frame_width:
                            print(f"block end error: [{y}][{x}/{frame_width}]")
                            continue
                        elif x == 0:
                            pass  # 空行
                        else:
                            x = frame_width  # 跳出循环

                elif block_type == TYPE_PIXELS:
                    # 01xxxxxx - 连续像素
                    count = b & 0x3F
                    for _ in range(count):
                        index = data[line_start]
                        line_start += 1
                        pixels[y][x] = self.palette[index] + (0x1F << 16)
                        x += 1

                elif block_type == TYPE_REPEAT:
                    # 10xxxxxx - 重复像素
                    count = b & 0x3F
                    index = data[line_start]
                    line_start += 1
                    c = self.palette[index]
                    for _ in range(count):
                        pixels[y][x] = c + (0x1F << 16)
                        x += 1

                elif block_type == TYPE_SKIP:
                    # 11xxxxxx - 跳过像素
                    count = b & 0x3F
                    x += count

            if x > frame_width:
                print(f"block end error: [{y}][{x}/{frame_width}]")

        return pixels


def load_was(filepath: str) -> WasImage:
    """
    从文件路径加载WAS文件

    Args:
        filepath: WAS文件路径

    Returns:
        WasImage 对象
    """
    with open(filepath, "rb") as f:
        data = f.read()
    parser = WasParser()
    return parser.parse(data)


def load_was_from_bytes(data: bytes) -> WasImage:
    """
    从字节数据加载WAS文件

    Args:
        data: WAS文件的二进制数据

    Returns:
        WasImage 对象
    """
    parser = WasParser()
    return parser.parse(data)


# ============================================================
# 简易测试
# ============================================================
if __name__ == "__main__":
    import sys
    if len(sys.argv) > 1:
        was = load_was(sys.argv[1])
        print(was)
        frames = was.get_all_frames_flat()
        print(f"总帧数: {len(frames)}")
        for idx, frame in enumerate(frames):
            print(f"  帧 {idx}: {frame.width}x{frame.height}, "
                  f"center=({frame.x_center},{frame.y_center}), "
                  f"delay={frame.delay}")
    else:
        print("用法: python was_parser.py <file.was>")
