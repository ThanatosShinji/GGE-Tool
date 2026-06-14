#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# MIT License - see LICENSE file in root directory
"""
MAP 文件格式解析器

支持两种格式:
  格式1 (1012.map):
    - 文件头 "0.1M" (4 bytes)
    - mapWidth, mapHeight, totalObjectCount
    - 8个数据段偏移 -> GNP图像 (1GNP标记)
    - blockCount, subAreaCount
    - 子区域偏移表 + 子区域描述
    - RLE指令数组 (int32编码)
    - 尾部标记 "GIRB"

  格式2 (1013.map):
    - 文件头 "0.1M" (4 bytes)
    - mapWidth, mapHeight, totalObjectCount
    - 8个数据段偏移 -> GEPJ图像 (GEPJ标记)
    - 7个额外偏移 (前2个数据索引, 后5个GEPJ图像)
    - 子区域描述 @0x50 (5个, 每个16字节)
    - RLE指令数组 (int32编码)
    - 16个GEPJ图像 (分散在文件中)
    - 尾部标记 "GIRB"
"""

import struct
import io
import os
from typing import List, Optional, Tuple


MAP_FILE_TAG = b"0.1M"
GNP_FILE_TAG = b"1GNP"
GEPJ_FILE_TAG = b"GEPJ"
GPJ2_FILE_TAG = b"2GPJ"
MAP_TAIL_TAG = b"GIRB"

# 所有已知的图像标记(按优先级排序)
ALL_IMAGE_TAGS = [GNP_FILE_TAG, GEPJ_FILE_TAG, GPJ2_FILE_TAG]

# RLE压缩类型 (每字节高2位)
TYPE_ALPHA = 0x00    # 00xxxxxx
TYPE_PIXELS = 0x40   # 01xxxxxx - 连续图块
TYPE_REPEAT = 0x80   # 10xxxxxx - 重复图块
TYPE_SKIP = 0xC0     # 11xxxxxx - 跳过
TYPE_FLAG = 0xC0


class MapSubArea:
    """地图子区域"""
    def __init__(self):
        self.index: int = 0
        self.count: int = 0          # 指令数
        self.data_offset: int = 0    # 数据偏移
        self.field2: int = 0         # 格式2中的额外字段
        self.field3: int = 0         # 格式2中的额外字段
        self.tile_ids: List[int] = []  # 解码后的图块ID列表


class MapImage:
    """地图中的GNP图像段"""
    def __init__(self):
        self.index: int = 0
        self.offset: int = 0
        self.width: int = 0
        self.height: int = 0
        self.jpeg_data: bytes = b''


class MapFile:
    """解析后的地图文件"""
    def __init__(self):
        self.filepath: str = ""
        self.version: str = ""
        self.map_width: int = 0
        self.map_height: int = 0
        self.total_object_count: int = 0
        self.block_count: int = 0
        self.sub_area_count: int = 0
        self.sub_areas: List[MapSubArea] = []
        self.images: List[MapImage] = []
        # 所有图块ID (扁平列表)
        self.all_tile_ids: List[int] = []
        # 格式类型
        self.format_version: int = 1  # 1=1012格式, 2=1013格式
        # 文件顺序的所有图像JPEG数据（包含偏移表未引用的，如GNP[0]/GEPJ[0]）
        self.file_order_jpegs: List[bytes] = []
        # 已解码的RGB图像（按文件顺序），由renderer填充
        self._decoded_rgb: list = None

    def get_image_count(self) -> int:
        return len(self.images)

    def get_image(self, index: int) -> Optional[MapImage]:
        if 0 <= index < len(self.images):
            return self.images[index]
        return None

    def save_image(self, index: int, output_path: str) -> bool:
        """保存指定索引的图像为文件"""
        img = self.get_image(index)
        if not img or not img.jpeg_data:
            return False
        # 自动确定扩展名
        if not output_path.lower().endswith(('.png', '.jpg', '.jpeg')):
            if img.jpeg_data[:4] == b'\x89PNG':
                output_path += '.png'
            elif img.jpeg_data[:2] == b'\xff\xd8':
                output_path += '.jpg'
            else:
                output_path += '.dat'
        img = self.get_image(index)
        if not img or not img.jpeg_data:
            return False
        with open(output_path, 'wb') as f:
            f.write(img.jpeg_data)
        return True

    def __str__(self):
        return (f"MapFile({self.map_width}x{self.map_height}, "
                f"objects={self.total_object_count}, "
                f"blocks={self.block_count}, sub_areas={self.sub_area_count}, "
                f"images={len(self.images)}, fmt=v{self.format_version})")


class MapParser:
    """MAP 文件解析器"""

    def __init__(self):
        pass

    def _read_uint32(self, data: bytes, offset: int) -> int:
        if offset < 0 or offset + 4 > len(data):
            return 0
        return struct.unpack_from('<I', data, offset)[0]

    def parse(self, data: bytes) -> MapFile:
        """
        解析 MAP 文件数据

        Args:
            data: MAP 文件的二进制数据

        Returns:
            MapFile 对象

        Raises:
            ValueError: 文件格式错误
        """
        if len(data) < 16:
            raise ValueError("文件太小，不是有效的MAP文件")

        # 检查文件头
        tag = data[0:4]
        if tag != MAP_FILE_TAG:
            raise ValueError(f"文件头标志错误: {tag!r} (期望 '0.1M')")

        result = MapFile()

        # 解析文件头
        result.map_width = self._read_uint32(data, 4)
        result.map_height = self._read_uint32(data, 8)
        result.total_object_count = self._read_uint32(data, 0x0C)

        # 读取8个数据段偏移
        segment_offsets = []
        for i in range(8):
            off = self._read_uint32(data, 0x10 + i * 4)
            segment_offsets.append(off)

        # 检测格式版本: 检查0x30处的值
        val_at_30 = self._read_uint32(data, 0x30)
        val_at_34 = self._read_uint32(data, 0x34)
        
        # 格式1 (1012.map): 0x30=52(block_count), 0x34=5(sub_count)
        # 格式2 (1013.map): 0x30和0x34是偏移(值很大, >90000)
        is_format2 = (val_at_30 > 50000 or val_at_34 > 50000)
        
        if is_format2:
            result.format_version = 2
            self._parse_format2(data, result, segment_offsets)
        else:
            result.format_version = 1
            self._parse_format1(data, result, segment_offsets)

        return result

    def _parse_format1(self, data: bytes, result: MapFile, segment_offsets: List[int]):
        """解析格式1 (1012.map)"""
        result.block_count = self._read_uint32(data, 0x30)
        result.sub_area_count = self._read_uint32(data, 0x34)
        
        # 限制子区域数量在合理范围（防止垃圾数据导致越界）
        max_possible = (len(data) - 0x38) // 4
        if result.sub_area_count > max_possible or result.sub_area_count > 50:
            result.sub_area_count = min(max_possible, 5)

        # 读取子区域偏移表
        sub_area_offsets = []
        for i in range(result.sub_area_count):
            off = self._read_uint32(data, 0x38 + i * 4)
            sub_area_offsets.append(off)

        # 读取子区域描述并解码图块数据
        result.sub_areas = []
        for i in range(result.sub_area_count):
            sub_off = sub_area_offsets[i]
            if sub_off + 16 > len(data):
                continue  # 无效偏移，跳过
            if sub_off < 0x38:
                continue  # 指向文件头，跳过
            try:
                sa = MapSubArea()
                sa.index = i
                sa.count = self._read_uint32(data, sub_off)
                data_off_rel = self._read_uint32(data, sub_off + 4)
                sa.field2 = self._read_uint32(data, sub_off + 8)
                sa.field3 = self._read_uint32(data, sub_off + 12)

                abs_off = sub_off + data_off_rel
                if abs_off > len(data):
                    continue
                sa.data_offset = abs_off

                sa.tile_ids = self._decode_sub_area_tiles(data, abs_off, sa.count)
                result.sub_areas.append(sa)
            except:
                continue

        # 合并所有图块ID
        for sa in result.sub_areas:
            result.all_tile_ids.extend(sa.tile_ids)

        # 提取GNP图像段 (从偏移表，尝试所有已知标记)
        result.images = []
        for tag in ALL_IMAGE_TAGS:
            if result.images:
                break
            for i, off in enumerate(segment_offsets):
                if off >= len(data):
                    continue
                img = self._extract_image(data, off, i, tag)
                if img:
                    result.images.append(img)

    def _parse_format2(self, data: bytes, result: MapFile, segment_offsets: List[int]):
        """解析格式2 (1013.map)"""
        # 计算 GIRB 尾部标记位置 (RLE 数据到此为止)
        girb_pos = data.find(MAP_TAIL_TAG)
        if girb_pos < 0:
            girb_pos = len(data)

        # 0x30-0x4B: 7个额外偏移
        extra_offsets = []
        for i in range(7):
            off = self._read_uint32(data, 0x30 + i * 4)
            extra_offsets.append(off)

        # 0x4C: 子区域描述总大小
        # 注意: 此值在大地图中可能是第8个额外偏移而不是描述大小
        # 检测方法: 如果值>50000则视为偏移，回退使用固定区域数(通常=5)
        desc_total_size = self._read_uint32(data, 0x4C)
        if desc_total_size > 50000:
            # 大地图: 0x4C是额外偏移的一部分，无独立子区域描述
            # 使用固定区域数或通过检测GIRB附近的RLE段结束来推断
            result.sub_area_count = 5  # 默认
        else:
            result.sub_area_count = desc_total_size // 16 if desc_total_size >= 16 else 5

        # 读取子区域描述 @0x50
        result.sub_areas = []
        for i in range(result.sub_area_count):
            off = 0x50 + i * 16
            sa = MapSubArea()
            sa.index = i
            sa.count = self._read_uint32(data, off)
            sa.data_offset = self._read_uint32(data, off + 4)
            sa.field2 = self._read_uint32(data, off + 8)
            sa.field3 = self._read_uint32(data, off + 12)

            # 解码该子区域的图块数据
            abs_off = sa.data_offset
            # 限制RLE读取范围: 不超过GIRB标记 (防读入JPEG垃圾数据)
            sa.tile_ids = self._decode_sub_area_tiles(data, abs_off, sa.count,
                                                       max_offset=girb_pos)
            result.sub_areas.append(sa)

        # 合并所有图块ID
        for sa in result.sub_areas:
            result.all_tile_ids.extend(sa.tile_ids)

        # 提取GEPJ图像段 (从偏移表 + 额外偏移，尝试所有标记)
        result.images = []
        target_tag = GEPJ_FILE_TAG
        for tag in ALL_IMAGE_TAGS:
            if result.images:
                target_tag = tag
                break
            for i, off in enumerate(segment_offsets):
                if off >= len(data):
                    continue
                img = self._extract_image(data, off, i, tag)
                if img:
                    result.images.append(img)
                    target_tag = tag
                    break
        
        # 再从7个额外偏移中提取（使用找到的标记）
        for i, off in enumerate(extra_offsets):
            if off >= len(data):
                continue
            img = self._extract_image(data, off, len(result.images), target_tag)
            if img:
                result.images.append(img)
        
        # 扫描全文件找遗漏的图像
        existing_offsets = {img.offset for img in result.images}
        pos = 0
        img_idx = len(result.images)
        while True:
            pos = data.find(target_tag, pos)
            if pos < 0:
                break
            if pos + 12 <= len(data):
                sz = struct.unpack_from('<I', data, pos + 4)[0]
                if sz > len(data) - pos - 8 or sz <= 0:
                    pos += 4
                    continue
                if pos not in existing_offsets:
                    img = self._extract_image_at(data, pos, img_idx)
                    if img:
                        result.images.append(img)
                        img_idx += 1
                pos += 8 + sz
                continue
            pos += 4

    def _decode_sub_area_tiles(self, data: bytes, abs_off: int, count: int,
                               max_offset: int = -1) -> List[int]:
        """
        解码子区域的图块数据

        每个子区域的数据是RLE指令数组, 每个指令编码为一个int32:
          - 最低字节(b0): RLE类型(高2位) + 计数(低6位)
          - 高3字节: 数据(alpha(1) + tile_id(1) + padding(1))

        RLE类型:
          00xxxxxx (ALPHA):
            00000000: 块结束 (跳过, 继续读取下一条指令)
            001xxxxx: 单个带alpha的图块 [alpha(1), tile_id(1)]
            000xxxxx (非0): alpha重复 [count=低5位, alpha(1), tile_id(1)]
          01xxxxxx (PIXELS): 连续图块 [count=低6位, tile_id(1)]
          10xxxxxx (REPEAT): 重复图块 [count=低6位, tile_id(1)]
          11xxxxxx (SKIP): 跳过 [count=低6位]

        注意: count 是指令数, 不是图块数
        图块ID为uint8 (1字节)

        Args:
            max_offset: 最大文件偏移 (防止读入GIRB之后的数据)
        """
        tile_ids = []
        pos = abs_off
        if max_offset < 0:
            max_offset = len(data)
        max_pos = min(len(data), max_offset)
        instructions_processed = 0

        while instructions_processed < count and pos + 4 <= max_pos:
            val = struct.unpack_from('<I', data, pos)[0]
            pos += 4
            instructions_processed += 1

            b0 = val & 0xFF
            data_val = (val >> 8) & 0xFFFFFF  # 高3字节

            block_type = b0 & TYPE_FLAG
            low6 = b0 & 0x3F

            if block_type == TYPE_ALPHA:
                if b0 == 0:
                    # 块结束 - 跳过, 继续下一条指令
                    continue
                elif (b0 & 0x20):  # 001xxxxx - 单个带alpha的图块
                    alpha = data_val & 0xFF
                    tile_id = (data_val >> 8) & 0xFF  # uint8
                    tile_ids.append(tile_id)
                else:  # 000xxxxx - alpha重复
                    repeat_count = low6
                    alpha = data_val & 0xFF
                    tile_id = (data_val >> 8) & 0xFF  # uint8
                    for _ in range(repeat_count):
                        tile_ids.append(tile_id)

            elif block_type == TYPE_PIXELS:
                # 01xxxxxx - 连续图块
                repeat_count = low6
                tile_id = data_val & 0xFF  # uint8
                for _ in range(repeat_count):
                    tile_ids.append(tile_id)

            elif block_type == TYPE_REPEAT:
                # 10xxxxxx - 重复图块
                repeat_count = low6
                tile_id = data_val & 0xFF  # uint8
                for _ in range(repeat_count):
                    tile_ids.append(tile_id)

            elif block_type == TYPE_SKIP:
                # 11xxxxxx - 跳过 (填充0)
                skip_count = low6
                for _ in range(skip_count):
                    tile_ids.append(0)

        return tile_ids

    def _extract_image(self, data: bytes, offset: int, index: int,
                       tag: bytes = GNP_FILE_TAG) -> Optional[MapImage]:
        """
        从指定偏移提取图像段 (支持GNP和GEPJ格式)

        格式:
          [前导信息...] '1GNP'/'GEPJ' [jpeg_size(4)] [JPEG数据...]
        """
        # 在附近查找标记
        tag_pos = data.find(tag, offset, offset + 64)
        if tag_pos < 0:
            return None

        return self._extract_image_at(data, tag_pos, index)

    @staticmethod
    def _fix_gepj_jpeg(jpeg_data: bytes) -> bytes:
        """
        修复 GEPJ 非标准 JPEG 数据，使其可被 PIL 正常解码

        只在检测到 GEPJ 特征 (FFA0 空标记) 时执行修复。

        GEPJ JPEG 存在 3 个问题:
          1. APP0标记 FF A0 是无长度字段的空标记
          2. SOS 段长度错误(应为12但只有9)，缺少 Ss/Se/AhAl 参数
          3. 熵编码数据段中的 0xFF 缺少字节填充 (byte-stuffing)

        注: 标准 JPEG (如 1012.map 的 GNP) 跳过此修复。
        """
        if len(jpeg_data) < 4:
            return jpeg_data

        # 检测是否为 GEPJ (非标准 FFA0 标记)
        if jpeg_data[2:4] != b'\xff\xa0':
            return jpeg_data  # 标准 JPEG，无需修复

        # 修复1: 剥离非标准 FFA0 APP0 空标记
        jpeg_data = jpeg_data[:2] + jpeg_data[4:]

        # 修复2: 修正 SOS 段参数
        old_sos = bytes([0xFF, 0xDA, 0x00, 0x09, 0x03,
                         0x01, 0x00, 0x02, 0x11, 0x03, 0x22])
        new_sos = bytes([0xFF, 0xDA, 0x00, 0x0C, 0x03,
                         0x01, 0x00, 0x02, 0x11, 0x03, 0x22,
                         0x00, 0x3F, 0x00])
        if old_sos in jpeg_data:
            jpeg_data = jpeg_data.replace(old_sos, new_sos, 1)

        # 找到 SOS 位置和真正的 EOI (最后一个 FF D9)
        i = 2
        sos_pos = -1
        last_eoi = jpeg_data.rfind(b'\xff\xd9')
        if last_eoi < 0:
            last_eoi = len(jpeg_data)

        while i < len(jpeg_data) - 1:
            if jpeg_data[i] == 0xFF:
                m = jpeg_data[i + 1]
                if m == 0xDA:
                    sos_pos = i
                    break
                elif m == 0xD9:
                    break
                elif 0xD0 <= m <= 0xD7:
                    i += 2
                elif m == 0x00:
                    i += 2
                else:
                    length = struct.unpack_from('>H', jpeg_data, i + 2)[0]
                    i += 2 + length
            else:
                i += 1

        if sos_pos < 0:
            return jpeg_data

        sl = struct.unpack_from('>H', jpeg_data, sos_pos + 2)[0]
        scan_start = sos_pos + 2 + sl

        # 修复3: 字节填充 (仅 GEPJ 需要)
        result = bytearray(jpeg_data[:scan_start])
        i = scan_start
        while i < len(jpeg_data):
            b = jpeg_data[i]
            if b == 0xFF:
                if i + 1 < len(jpeg_data):
                    nb = jpeg_data[i + 1]
                    if i == last_eoi:
                        result.extend([0xFF, 0xD9])
                        i += 2
                        break
                    result.extend([0xFF, 0x00])
                    i += 1
                else:
                    result.extend([0xFF, 0x00])
                    i += 1
            else:
                result.append(b)
                i += 1

        if i < len(jpeg_data):
            result.extend(jpeg_data[i:])

        return bytes(result)

    def _extract_image_at(self, data: bytes, tag_pos: int, index: int) -> Optional[MapImage]:
        """在指定标记位置提取图像"""
        if tag_pos + 8 > len(data):
            return None

        jpeg_size = self._read_uint32(data, tag_pos + 4)
        if jpeg_size <= 0 or tag_pos + 8 + jpeg_size > len(data):
            return None

        jpeg_data = data[tag_pos + 8 : tag_pos + 8 + jpeg_size]

        # 验证JPEG头尾
        if jpeg_data[:2] != b'\xff\xd8' or jpeg_data[-2:] != b'\xff\xd9':
            return None

        # 修复 GEPJ 非标准 JPEG 并转为标准 PNG 数据
        jpeg_data = self._fix_gepj_jpeg(jpeg_data)

        img = MapImage()
        img.index = index
        img.offset = tag_pos
        img.jpeg_data = jpeg_data

        # 尝试从图像数据获取尺寸
        try:
            from PIL import Image
            import io
            pil_img = Image.open(io.BytesIO(jpeg_data))
            img.width, img.height = pil_img.size
        except Exception:
            img.width = 0
            img.height = 0

        return img


def load_map(filepath: str) -> MapFile:
    """
    从文件路径加载MAP文件

    Args:
        filepath: MAP文件路径

    Returns:
        MapFile 对象
    """
    with open(filepath, "rb") as f:
        data = f.read()
    parser = MapParser()
    result = parser.parse(data)
    result.filepath = filepath
    _populate_file_order_jpegs(data, result)
    return result


def load_map_from_bytes(data: bytes) -> MapFile:
    """
    从字节数据加载MAP文件

    Args:
        data: MAP文件的二进制数据

    Returns:
        MapFile 对象
    """
    parser = MapParser()
    result = parser.parse(data)
    _populate_file_order_jpegs(data, result)
    return result


def _populate_file_order_jpegs(data: bytes, mf: MapFile):
    """扫描原始数据，按文件顺序提取所有图像 JPEG 字节（支持所有标记格式）"""
    import struct
    # 尝试所有图像标记
    mf.file_order_jpegs = []
    for tag in ALL_IMAGE_TAGS:
        if mf.file_order_jpegs:
            break
        pos = 0
        while True:
            pos = data.find(tag, pos)
            if pos < 0:
                break
            if pos + 12 > len(data):
                pos += 1
                continue
            sz = struct.unpack_from('<I', data, pos + 4)[0]
            if sz <= 0 or pos + 8 + sz > len(data):
                pos += 1
                continue
            jpeg_data = data[pos + 8:pos + 8 + sz]
            mf.file_order_jpegs.append(jpeg_data)
            pos += 8 + sz


# ============================================================
# 简易测试
# ============================================================
if __name__ == "__main__":
    import sys
    if len(sys.argv) > 1:
        mf = load_map(sys.argv[1])
        print(mf)
        print(f"  子区域数: {len(mf.sub_areas)}")
        for sa in mf.sub_areas:
            non_zero = sum(1 for t in sa.tile_ids if t != 0)
            print(f"  子区域{sa.index}: {len(sa.tile_ids)}图块, {non_zero}非零, "
                  f"范围=[{sa.field2}x{sa.field3}]")
        print(f"  图像段数: {len(mf.images)}")
        for img in mf.images:
            print(f"    图像{img.index}: {img.width}x{img.height}, "
                  f"{len(img.jpeg_data)} bytes")
        # 保存图像
        for img in mf.images:
            ext = ".png" if img.jpeg_data[:4] == b'\x89PNG' else ".jpg"
            out_name = f"map_img_{img.index}{ext}"
            mf.save_image(img.index, out_name)
            print(f"    已保存: {out_name}")
    else:
        print("用法: python map_parser.py <file.map>")
