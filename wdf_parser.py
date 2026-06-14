#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# MIT License - see LICENSE file in root directory
"""
WDF 文件格式解析器
参考: https://github.com/duguaosnowqrl/wastools (Java实现)

WDF 文件格式:
  - 文件头: "PFDW" (4 bytes)
  - fileCount (4 bytes, int32 LE)
  - indexOffset (4 bytes, int32 LE) — 索引表偏移
  - [文件数据区] — 各子文件数据连续存放
  - 索引表:
    - 每个条目 16 bytes:
      - id (4 bytes, int32 LE) — 文件ID
      - offset (4 bytes, int32 LE) — 文件数据偏移
      - size (4 bytes, int32 LE) — 文件数据大小
      - space (4 bytes, int32 LE) — 文件占用空间 (通常=size)
  - [可选] INI配置文件: Resources/namelists/<filename>.ini
    - [Resource]
    - HEX_ID=别名
"""

import os
import struct
from typing import Dict, List, Optional, Tuple


WDF_FILE_TAG = b"PFDW"
WDF_ENTRY_SIZE = 16  # 每个索引条目16字节


class WasFileNode:
    """WDF 中的子文件节点"""
    def __init__(self):
        self.id: int = 0           # 文件ID
        self.offset: int = 0       # 文件数据在WDF中的偏移
        self.size: int = 0         # 文件数据大小
        self.space: int = 0        # 文件占用空间
        self.name: Optional[str] = None  # 别名 (从INI加载)
        self.parent: Optional['WdfFile'] = None  # 所属WDF

    @property
    def id_hex(self) -> str:
        """返回ID的十六进制表示 (大写, 无0x前缀)"""
        return f"{self.id:08X}"

    def __repr__(self):
        name_str = f" ({self.name})" if self.name else ""
        return f"WasFileNode[0x{self.id:08X}]{name_str} off=0x{self.offset:X} size={self.size}"


class WdfFile:
    """WDF 资源集合文件"""
    def __init__(self):
        self.filepath: str = ""
        self.file_count: int = 0
        self.file_list: List[WasFileNode] = []
        self.file_mapping: Dict[int, WasFileNode] = {}  # id -> node

    def load(self, filepath: str, ini_dir: Optional[str] = None) -> None:
        """
        加载 WDF 文件

        Args:
            filepath: WDF 文件路径
            ini_dir: INI配置文件目录 (默认: 自动查找 Resources/namelists/)
        """
        self.filepath = filepath

        with open(filepath, "rb") as f:
            data = f.read()

        # 检查文件头
        tag = data[:4]
        if tag != WDF_FILE_TAG:
            raise ValueError(f"文件头标志错误: {tag!r} (期望 'PFDW')")

        # 读取文件数和索引表偏移
        self.file_count = struct.unpack_from('<I', data, 4)[0]
        index_offset = struct.unpack_from('<I', data, 8)[0]

        # 读取索引表
        self.file_list = []
        self.file_mapping = {}
        for i in range(self.file_count):
            entry_off = index_offset + i * WDF_ENTRY_SIZE
            if entry_off + WDF_ENTRY_SIZE > len(data):
                break

            node = WasFileNode()
            node.id = struct.unpack_from('<I', data, entry_off)[0]
            node.offset = struct.unpack_from('<I', data, entry_off + 4)[0]
            node.size = struct.unpack_from('<I', data, entry_off + 8)[0]
            node.space = struct.unpack_from('<I', data, entry_off + 12)[0]
            node.parent = self

            self.file_list.append(node)
            self.file_mapping[node.id] = node

        # 加载INI配置文件 (别名)
        if ini_dir:
            self._load_ini(ini_dir)
        else:
            # 自动查找 INI 文件
            auto_ini = self._find_ini_path(filepath)
            if auto_ini and os.path.exists(auto_ini):
                self._load_ini_file(auto_ini)

    def _find_ini_path(self, wdf_path: str) -> Optional[str]:
        """自动查找 INI 文件路径"""
        basename = os.path.basename(wdf_path)
        # 尝试 Resources/namelists/<filename>.ini
        candidates = [
            os.path.join(os.path.dirname(wdf_path), "Resources", "namelists", f"{basename}.ini"),
            os.path.join(os.path.dirname(wdf_path), f"{basename}.ini"),
            os.path.join(os.path.dirname(wdf_path), os.path.splitext(basename)[0] + ".ini"),
        ]
        for path in candidates:
            if os.path.exists(path):
                return path
        return None

    def _load_ini(self, ini_dir: str) -> None:
        """从目录加载 INI 文件"""
        basename = os.path.basename(self.filepath)
        ini_path = os.path.join(ini_dir, f"{basename}.ini")
        if os.path.exists(ini_path):
            self._load_ini_file(ini_path)

    def _load_ini_file(self, ini_path: str) -> None:
        """
        加载 INI 配置文件 (别名映射)

        INI 格式:
        [Resource]
        FE62F503=别名1
        FE62F504=别名2
        """
        print(f"[*] 加载别名配置: {ini_path}")
        with open(ini_path, "r", encoding="utf-8") as f:
            content = f.read()

        lines = content.splitlines()
        in_resource = False
        for line in lines:
            line = line.strip()
            if not line:
                continue
            if line == "[Resource]":
                in_resource = True
                continue
            if in_resource and "=" in line:
                hex_id, alias = line.split("=", 1)
                hex_id = hex_id.strip()
                alias = alias.strip()
                try:
                    uid = int(hex_id, 16)
                    if uid in self.file_mapping:
                        self.file_mapping[uid].name = alias
                except ValueError:
                    pass

    def get_file_data(self, node: WasFileNode) -> Optional[bytes]:
        """获取子文件的原始数据"""
        with open(self.filepath, "rb") as f:
            f.seek(node.offset)
            return f.read(node.size)

    def get_file_data_by_id(self, uid: int) -> Optional[bytes]:
        """通过ID获取子文件数据"""
        node = self.file_mapping.get(uid)
        if node:
            return self.get_file_data(node)
        return None

    def extract_all(self, output_dir: str, as_was: bool = False) -> int:
        """
        提取所有子文件到指定目录

        Args:
            output_dir: 输出目录
            as_was: 如果为True, 添加.was扩展名

        Returns:
            提取的文件数量
        """
        os.makedirs(output_dir, exist_ok=True)
        count = 0
        for node in self.file_list:
            data = self.get_file_data(node)
            if data is None:
                continue

            # 生成文件名
            if node.name:
                filename = node.name
            else:
                filename = node.id_hex

            if as_was:
                filename += ".was"

            filepath = os.path.join(output_dir, filename)
            with open(filepath, "wb") as f:
                f.write(data)
            count += 1

        return count

    def get_file_list_sorted(self, sort_by: str = "id") -> List[WasFileNode]:
        """
        获取排序后的文件列表

        Args:
            sort_by: 排序方式, "id" / "name" / "size"
        """
        if sort_by == "id":
            return sorted(self.file_list, key=lambda n: n.id)
        elif sort_by == "name":
            return sorted(self.file_list, key=lambda n: n.name or n.id_hex)
        elif sort_by == "size":
            return sorted(self.file_list, key=lambda n: n.size)
        return self.file_list

    def print_info(self) -> None:
        """打印 WDF 文件信息"""
        print("=" * 60)
        print(f"  WDF 文件: {os.path.basename(self.filepath)}")
        print("=" * 60)
        print(f"  文件数: {self.file_count}")
        print(f"  文件大小: {os.path.getsize(self.filepath):,} bytes")
        print()
        print(f"  {'ID':>10} {'别名':<20} {'偏移':>10} {'大小':>8}")
        print(f"  {'-'*10} {'-'*20} {'-'*10} {'-'*8}")
        for node in self.get_file_list_sorted("id"):
            name = node.name if node.name else ""
            print(f"  0x{node.id:08X} {name:<20} 0x{node.offset:08X} {node.size:>8}")

    def __repr__(self):
        return f"WdfFile({os.path.basename(self.filepath)}, files={self.file_count})"


def load_wdf(filepath: str, ini_dir: Optional[str] = None) -> WdfFile:
    """
    加载 WDF 文件

    Args:
        filepath: WDF 文件路径
        ini_dir: INI配置文件目录 (可选)

    Returns:
        WdfFile 对象
    """
    wdf = WdfFile()
    wdf.load(filepath, ini_dir)
    return wdf


# ============================================================
# 简易测试
# ============================================================
if __name__ == "__main__":
    import sys
    if len(sys.argv) > 1:
        wdf = load_wdf(sys.argv[1])
        wdf.print_info()

        # 检查每个子文件是否为WAS格式
        print(f"\n  子文件类型检测:")
        for node in wdf.file_list:
            data = wdf.get_file_data(node)
            if data and len(data) >= 2:
                tag = data[:2]
                is_was = tag in (b"SP", b"SH")
                print(f"    0x{node.id:08X}: {tag!r} {'(WAS)' if is_was else '(其他)'}")
    else:
        print("用法: python wdf_parser.py <file.wdf>")
