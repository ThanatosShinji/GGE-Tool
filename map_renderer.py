#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# MIT License - see LICENSE file in root directory
"""
MAP 文件渲染器

加载所有 GNP/GEPJ 图像，按文件出现顺序自然排列到矩形网格中，
居中放置到地图画布上，超出裁剪，不足留黑边。

网格尺寸自动优化：优先填满无空位，同时匹配地图宽高比。
"""
import os
import numpy as np
import cv2
from PIL import Image


def _get_decoded_images(mf):
    """从 MapFile 获取已解码 RGB 图像（按文件顺序），带缓存"""
    if mf._decoded_rgb is not None:
        return mf._decoded_rgb

    from map_parser import MapParser, ALL_IMAGE_TAGS
    # 判断是否需要 GEPJ 修复：检测JPEG头是否有 FFA0 特征
    # 检查第一个文件顺序图像是否有 FFA0 标记
    needs_fix = bool(mf.file_order_jpegs and
                     len(mf.file_order_jpegs[0]) >= 4 and
                     mf.file_order_jpegs[0][2:4] == b'\xff\xa0')

    images = []
    for jpeg_data in mf.file_order_jpegs:
        if needs_fix:
            jpeg_data = MapParser._fix_gepj_jpeg(jpeg_data)
        nparr = np.frombuffer(jpeg_data, np.uint8)
        bgr = cv2.imdecode(nparr, cv2.IMREAD_COLOR)
        if bgr is None:
            bgr = np.zeros((240, 320, 3), dtype=np.uint8)
        rgb = cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB)
        images.append(rgb)

    mf._decoded_rgb = images
    return images


def _center_to_canvas(mosaic, canvas_w, canvas_h):
    """将马赛克居中放置到画布上，裁剪超出部分"""
    canvas = np.zeros((canvas_h, canvas_w, 3), dtype=np.uint8)
    mh, mw = mosaic.shape[:2]
    ox = (canvas_w - mw) // 2
    oy = (canvas_h - mh) // 2
    sx1 = max(0, -ox)
    sy1 = max(0, -oy)
    sx2 = min(mw, canvas_w - ox)
    sy2 = min(mh, canvas_h - oy)
    dx1 = max(0, ox)
    dy1 = max(0, oy)
    if sx2 > sx1 and sy2 > sy1:
        canvas[dy1:dy1 + (sy2 - sy1), dx1:dx1 + (sx2 - sx1)] = \
            mosaic[sy1:sy2, sx1:sx2]
    return canvas


def _best_grid(n, map_w=None, map_h=None):
    """找到最合适的网格尺寸 (cols, rows)"""
    best = None
    best_empty = 999
    best_ar_diff = 999.0
    map_ar = map_w / map_h if map_w and map_h else 1.0
    for cols in range(1, n + 1):
        rows = (n + cols - 1) // cols
        empty = cols * rows - n
        grid_ar = (cols * 320) / (rows * 240) if rows > 0 else 0
        ar_diff = abs(grid_ar - map_ar)
        better = False
        if empty < best_empty:
            better = True
        elif empty == best_empty and ar_diff < best_ar_diff:
            better = True
        elif empty == best_empty and abs(ar_diff - best_ar_diff) < 0.01 and cols > best[0]:
            better = True
        if better:
            best = (cols, rows)
            best_empty = empty
            best_ar_diff = ar_diff
    return best


def render_map(map_path, output_path=None, tile_size=1, mf=None):
    """
    加载 MAP 文件中所有 JPEG，按文件出现顺序拼成网格，居中到地图画布。

    Args:
        map_path: MAP 文件路径（已加载 mf 时可传 None）
        output_path: 可选输出图片路径
        tile_size: 缩放倍数（目前仅支持 1）
        mf: 可选，已解析的 MapFile 对象（避免重复加载）
    """
    from map_parser import load_map

    if mf is None:
        mf = load_map(map_path)

    images = _get_decoded_images(mf)
    mw = mf.map_width * tile_size
    mh = mf.map_height * tile_size

    n = len(images)
    if n == 0:
        raise ValueError("没有找到任何图像")

    ih, iw = images[0].shape[:2]
    cols, rows = _best_grid(n, mw, mh)

    mosaic = np.zeros((rows * ih, cols * iw, 3), dtype=np.uint8)
    for idx, img in enumerate(images):
        if idx >= rows * cols:
            break
        r = idx // cols
        c = idx % cols
        mosaic[r * ih:(r + 1) * ih, c * iw:(c + 1) * iw] = img

    canvas = _center_to_canvas(mosaic, mw, mh)
    result = Image.fromarray(canvas, "RGB")
    if output_path:
        result.save(output_path)
        print(f"[+] 已保存: {output_path} ({result.size})")
    return result


if __name__ == "__main__":
    import sys
    if len(sys.argv) > 1:
        path = sys.argv[1]
        ts = int(sys.argv[2]) if len(sys.argv) > 2 else 1
        out = sys.argv[3] if len(sys.argv) > 3 else None
        if not out:
            out = os.path.splitext(os.path.basename(path))[0] + "_map.png"
        render_map(path, out, ts)
    else:
        print("用法: python map_renderer.py <file.map> [tile_size] [output.png]")
