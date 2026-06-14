#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# MIT License - see LICENSE file in root directory
"""
WAS/WDF 文件查看器
支持:
  - 显示WAS/WDF文件结构信息
  - 导出帧为PNG图片
  - 导出所有帧为GIF动画
  - 从WDF中提取/查看WAS文件
"""

import os
import sys
import struct

# 添加当前目录到路径
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from PIL import Image

from was_parser import load_was, load_was_from_bytes, WasImage, WasFrame
from wdf_parser import load_wdf, WdfFile


def _get_was_bounds(was: WasImage):
    """计算 WAS 所有帧在画布上的实际包围盒"""
    x_min = y_min = float('inf')
    x_max = y_max = float('-inf')
    for i in range(was.sprite_count):
        for n in range(was.frame_count):
            f = was.get_frame(i, n)
            if f and f.pixels:
                fx0 = was.x_center - f.x_center
                fy0 = was.y_center - f.y_center
                fx1 = fx0 + f.width
                fy1 = fy0 + f.height
                if fx0 < x_min: x_min = fx0
                if fy0 < y_min: y_min = fy0
                if fx1 > x_max: x_max = fx1
                if fy1 > y_max: y_max = fy1
    if x_min == float('inf'):
        return 0, 0, was.width, was.height
    return int(x_min), int(y_min), int(x_max), int(y_max)


def render_frame_to_pil(was: WasImage, frame: WasFrame, scale: int = 1) -> Image.Image:
    """
    将 WAS 帧渲染到 PIL Image (完整画布, 对齐中心点)
    使用 numpy 向量化操作
    """
    import numpy as np

    bx0, by0, bx1, by1 = _get_was_bounds(was)
    canvas_w = (bx1 - bx0) * scale
    canvas_h = (by1 - by0) * scale
    if not frame or not frame.pixels:
        return Image.new("RGBA", (canvas_w, canvas_h), (0, 0, 0, 0))

    argb = frame.to_argb_pixels()
    ys, xs = np.meshgrid(np.arange(frame.height), np.arange(frame.width), indexing='ij')
    canvas_xs = (was.x_center - frame.x_center + xs - bx0) * scale
    canvas_ys = (was.y_center - frame.y_center + ys - by0) * scale

    mask = (canvas_xs >= 0) & (canvas_xs < canvas_w) & (canvas_ys >= 0) & (canvas_ys < canvas_h)
    if not np.any(mask):
        return Image.new("RGBA", (canvas_w, canvas_h), (0, 0, 0, 0))

    valid_xs = canvas_xs[mask]
    valid_ys = canvas_ys[mask]
    valid_pixels = argb[mask]

    if scale == 1:
        canvas_arr = np.zeros((canvas_h, canvas_w, 4), dtype=np.uint8)
        canvas_arr[valid_ys, valid_xs] = valid_pixels[:, [1, 2, 3, 0]]
        return Image.fromarray(canvas_arr, "RGBA")
    else:
        rgba = valid_pixels[:, [1, 2, 3, 0]]
        canvas_arr = np.zeros((canvas_h, canvas_w, 4), dtype=np.uint8)
        for dy in range(scale):
            for dx in range(scale):
                canvas_arr[valid_ys + dy, valid_xs + dx] = rgba
        return Image.fromarray(canvas_arr, "RGBA")


def render_frame_to_photo(was: WasImage, frame: WasFrame, max_size: int = 512):
    """渲染帧为 tkinter 可用的 PhotoImage"""
    from PIL import ImageTk
    bx0, by0, bx1, by1 = _get_was_bounds(was)
    bw, bh = bx1 - bx0, by1 - by0
    scale = 1 if (bw <= max_size and bh <= max_size) else max(1, int(min(max_size / bw, max_size / bh)))
    pil_img = render_frame_to_pil(was, frame, scale)
    if pil_img.width > max_size or pil_img.height > max_size:
        r = min(max_size / pil_img.width, max_size / pil_img.height)
        pil_img = pil_img.resize((int(pil_img.width * r), int(pil_img.height * r)), Image.NEAREST)
    return ImageTk.PhotoImage(pil_img)


def print_was_info(was: WasImage):
    """打印WAS文件信息"""
    print("=" * 60)
    print("  WAS 文件信息")
    print("=" * 60)
    print(f"  精灵数 (Sprite Count):  {was.sprite_count}")
    print(f"  帧数   (Frame Count):   {was.frame_count}")
    print(f"  宽度   (Width):         {was.width}")
    print(f"  高度   (Height):        {was.height}")
    print(f"  中心点 (Center):        ({was.x_center}, {was.y_center})")
    print(f"  调色板 (Palette):       256 色")
    print()

    # 打印调色板前16色
    print("  调色板 (前16色, RGB565):")
    for i in range(0, 16, 4):
        row = []
        for j in range(4):
            idx = i + j
            val = was.palette[idx]
            r = (val >> 11) & 0x1F
            g = (val >> 5) & 0x3F
            b = val & 0x1F
            row.append(f"  [{idx:3d}] 0x{val:04X} (R{r:2d} G{g:2d} B{b:2d})")
        print("     " + " | ".join(row))
    print()

    # 打印帧信息
    print(f"  帧详情:")
    for i in range(was.sprite_count):
        for n in range(was.frame_count):
            frame = was.get_frame(i, n)
            if frame:
                has_pixels = frame.pixels is not None
                pixel_info = ""
                if has_pixels and frame.pixels:
                    # 统计非透明像素
                    non_zero = sum(1 for row in frame.pixels for p in row if p != 0)
                    pixel_info = f", 有效像素={non_zero}"
                print(f"    精灵[{i}]帧[{n}]: "
                      f"{frame.width}x{frame.height}, "
                      f"center=({frame.x_center},{frame.y_center}), "
                      f"delay={frame.delay}{pixel_info}")


def export_frame_png(was: WasImage, sprite_idx: int, frame_idx: int,
                     output_path: str, scale: int = 1):
    """
    导出指定帧为PNG图片

    Args:
        was: WasImage对象
        sprite_idx: 精灵索引
        frame_idx: 帧索引
        output_path: 输出PNG文件路径
        scale: 缩放倍数 (默认1)
    """
    try:
        from PIL import Image
    except ImportError:
        print("[!] 需要安装 Pillow: pip install Pillow")
        return False

    frame = was.get_frame(sprite_idx, frame_idx)
    if not frame or not frame.pixels:
        print(f"[!] 帧不存在: sprite={sprite_idx}, frame={frame_idx}")
        return False

    # 使用 numpy 向量化渲染 (基于实际包围盒)
    import numpy as np

    bx0, by0, bx1, by1 = _get_was_bounds(was)
    canvas_w = (bx1 - bx0) * scale
    canvas_h = (by1 - by0) * scale
    argb = frame.to_argb_pixels()  # (H, W, 4) a,r,g,b

    ys, xs = np.meshgrid(np.arange(frame.height), np.arange(frame.width), indexing='ij')
    canvas_xs = (was.x_center - frame.x_center + xs - bx0) * scale
    canvas_ys = (was.y_center - frame.y_center + ys - by0) * scale

    mask = (canvas_xs >= 0) & (canvas_xs < canvas_w) & (canvas_ys >= 0) & (canvas_ys < canvas_h)
    if np.any(mask):
        valid_xs = canvas_xs[mask]
        valid_ys = canvas_ys[mask]
        valid_pixels = argb[mask][:, [1, 2, 3, 0]]  # 转 r,g,b,a

        canvas_arr = np.zeros((canvas_h, canvas_w, 4), dtype=np.uint8)
        if scale == 1:
            canvas_arr[valid_ys, valid_xs] = valid_pixels
        else:
            for dy in range(scale):
                for dx in range(scale):
                    canvas_arr[valid_ys + dy, valid_xs + dx] = valid_pixels

        img = Image.fromarray(canvas_arr, "RGBA")
    else:
        img = Image.new("RGBA", (canvas_w, canvas_h), (0, 0, 0, 0))

    img.save(output_path, "PNG")
    print(f"[+] 已导出: {output_path} ({img.size})")
    return True


def export_all_frames(was: WasImage, output_dir: str, scale: int = 1):
    """
    导出所有帧为PNG图片

    Args:
        was: WasImage对象
        output_dir: 输出目录
        scale: 缩放倍数
    """
    os.makedirs(output_dir, exist_ok=True)
    count = 0
    for i in range(was.sprite_count):
        for n in range(was.frame_count):
            filename = f"sprite{i}_frame{n}.png"
            filepath = os.path.join(output_dir, filename)
            if export_frame_png(was, i, n, filepath, scale):
                count += 1
    print(f"[+] 共导出 {count} 帧到: {output_dir}")


def export_gif(was: WasImage, output_path: str, scale: int = 1,
               loop: int = 0, duration: int = 100, sprite_idx: int = None):
    """
    导出指定精灵或所有帧为GIF动画（使用与UI相同的渲染函数，保证画面一致）

    Args:
        was: WasImage对象
        output_path: 输出GIF文件路径
        scale: 缩放倍数
        loop: 循环次数 (0=无限)
        duration: 每帧显示时间(毫秒)
        sprite_idx: 精灵索引，None=导出所有精灵
    """

    frames_pil = []
    sprites = [sprite_idx] if sprite_idx is not None else range(was.sprite_count)
    for i in sprites:
        for n in range(was.frame_count):
            frame = was.get_frame(i, n)
            if not frame or not frame.pixels:
                continue
            img = render_frame_to_pil(was, frame, scale)
            frames_pil.append(img.convert("RGBA"))

    if frames_pil:
        # 用第一帧作为调色板基准，其余帧共享同一调色板
        frames_rgba = frames_pil
        # 扔掉 alpha 通道（GIF 不支持半透明），以黑色背景合成
        frames_rgb = []
        for f in frames_rgba:
            bg = Image.new("RGB", f.size, (0, 0, 0))
            bg.paste(f, mask=f.split()[3])  # 用 alpha 通道做遮罩
            frames_rgb.append(bg)

        # 共享同一调色板（用第一帧生成）
        palette_img = frames_rgb[0].quantize(colors=256, method=Image.Quantize.MEDIANCUT)
        frames_quantized = [palette_img]
        for f in frames_rgb[1:]:
            qi = f.quantize(colors=256, method=Image.Quantize.MEDIANCUT, palette=palette_img)
            frames_quantized.append(qi)

        frames_quantized[0].save(
            output_path,
            save_all=True,
            append_images=frames_quantized[1:],
            loop=loop,
            duration=duration,
            disposal=2
        )
        print(f"[+] 已导出GIF: {output_path} ({len(frames_quantized)} 帧)")
        return True
    return False


def export_raw_pixels(was: WasImage, output_path: str):
    """
    导出帧像素数据为文本格式 (调试用)
    """
    with open(output_path, "w", encoding="utf-8") as f:
        f.write(f"WAS Image Info:\n")
        f.write(f"  Sprites: {was.sprite_count}\n")
        f.write(f"  Frames:  {was.frame_count}\n")
        f.write(f"  Size:    {was.width}x{was.height}\n")
        f.write(f"  Center:  ({was.x_center}, {was.y_center})\n")
        f.write(f"\nPalette (first 32):\n")
        for i in range(32):
            val = was.palette[i]
            r = (val >> 11) & 0x1F
            g = (val >> 5) & 0x3F
            b = val & 0x1F
            f.write(f"  [{i:3d}] 0x{val:04X}  R={r:2d} G={g:2d} B={b:2d}\n")

        for i in range(was.sprite_count):
            for n in range(was.frame_count):
                frame = was.get_frame(i, n)
                if not frame or not frame.pixels:
                    continue
                f.write(f"\n--- Sprite[{i}] Frame[{n}] ({frame.width}x{frame.height}) ---\n")
                for y in range(min(frame.height, 50)):  # 最多50行
                    row_str = ""
                    for x in range(min(frame.width, 80)):  # 最多80列
                        p = frame.pixels[y][x]
                        if p == 0:
                            row_str += ".."
                        else:
                            alpha = (p >> 16) & 0x1F
                            if alpha > 0:
                                row_str += "XX"
                            else:
                                row_str += "::"
                    f.write(f"  {y:3d}: {row_str}\n")

    print(f"[+] 已导出像素文本: {output_path}")


def main():
    import argparse

    parser = argparse.ArgumentParser(description="WAS/WDF 文件查看器")
    parser.add_argument("file", help="WAS 或 WDF 文件路径")
    parser.add_argument("--info", action="store_true", help="显示文件信息")
    parser.add_argument("--export-png", metavar="DIR", help="导出所有帧为PNG到指定目录")
    parser.add_argument("--export-gif", metavar="FILE", help="导出为GIF动画")
    parser.add_argument("--export-txt", metavar="FILE", help="导出像素数据为文本")
    parser.add_argument("--scale", type=int, default=1, help="缩放倍数 (默认1)")
    parser.add_argument("--duration", type=int, default=100, help="GIF每帧时长(毫秒, 默认100)")
    parser.add_argument("--extract", metavar="DIR", help="从WDF中提取所有子文件")
    parser.add_argument("--index", type=int, default=None,
                        help="WDF中指定索引的子文件 (默认: 全部)")
    parser.add_argument("--id", dest="hex_id", metavar="HEX",
                        help="WDF中指定ID的子文件 (十六进制)")

    args = parser.parse_args()

    if not os.path.exists(args.file):
        print(f"[!] 文件不存在: {args.file}")
        sys.exit(1)

    # 检测文件类型
    with open(args.file, "rb") as f:
        magic = f.read(4)

    if magic[:2] in (b"SP", b"SH"):
        # WAS 文件
        print(f"[*] 加载 WAS: {args.file}")
        was = load_was(args.file)
        print_was_info(was)

        if args.export_png:
            export_all_frames(was, args.export_png, args.scale)
        if args.export_gif:
            export_gif(was, args.export_gif, args.scale, duration=args.duration)
        if args.export_txt:
            export_raw_pixels(was, args.export_txt)

    elif magic == b"PFDW":
        # WDF 文件
        print(f"[*] 加载 WDF: {args.file}")
        wdf = load_wdf(args.file)
        wdf.print_info()

        if args.extract:
            count = wdf.extract_all(args.extract, as_was=True)
            print(f"[+] 已提取 {count} 个文件到: {args.extract}")

        # 如果指定了索引或ID，查看对应的WAS
        target_nodes = []
        if args.hex_id is not None:
            uid = int(args.hex_id, 16)
            node = wdf.file_mapping.get(uid)
            if node:
                target_nodes.append(node)
            else:
                print(f"[!] 未找到ID=0x{args.hex_id} 的文件")
        elif args.index is not None:
            if 0 <= args.index < len(wdf.file_list):
                target_nodes.append(wdf.file_list[args.index])
            else:
                print(f"[!] 索引越界: {args.index} (共 {len(wdf.file_list)} 个)")
        else:
            target_nodes = wdf.file_list

        for node in target_nodes:
            data = wdf.get_file_data(node)
            if data and len(data) >= 2 and data[:2] in (b"SP", b"SH"):
                name = node.name or node.id_hex
                print(f"\n{'='*60}")
                print(f"  WDF 子文件: 0x{node.id:08X} ({name})")
                print(f"{'='*60}")
                try:
                    was = load_was_from_bytes(data)
                    print_was_info(was)

                    if args.export_png:
                        subdir = os.path.join(args.export_png, name)
                        export_all_frames(was, subdir, args.scale)
                    if args.export_gif:
                        gif_path = args.export_gif
                        if len(target_nodes) > 1:
                            base, ext = os.path.splitext(gif_path)
                            gif_path = f"{base}_{name}{ext}"
                        export_gif(was, gif_path, args.scale, duration=args.duration)
                    if args.export_txt:
                        txt_path = args.export_txt
                        if len(target_nodes) > 1:
                            base, ext = os.path.splitext(txt_path)
                            txt_path = f"{base}_{name}{ext}"
                        export_raw_pixels(was, txt_path)
                except Exception as e:
                    print(f"  [!] WAS解析失败: {e}")
    else:
        print(f"[!] 未知文件格式: {magic!r}")
        print(f"    期望: 'SP'/'SH' (WAS) 或 'PFDW' (WDF)")
        sys.exit(1)


if __name__ == "__main__":
    main()
