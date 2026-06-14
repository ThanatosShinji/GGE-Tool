# GGE-Tool

一款针对 **GGE** (Game Graphics Engine) 游戏引擎资源文件的多格式素材查看器与解析工具。

支持 **WAS**（精灵/动画）、**WDF**（WAS 存档）和 **MAP**（场景/地图）三种文件格式。

![GGE-Tool 截图](screenshot.png)

## 功能

- **WAS 查看器** — 浏览精灵帧、播放动画、导出 PNG/GIF
- **WDF 浏览器** — 从 WDF 存档中浏览和提取 WAS 文件
- **MAP 查看器** — 解析和渲染游戏地图 (.map)，支持：
  - JPEG 网格布局自动检测（GNP / GEPJ / 2GPJ 格式）
  - 通过段偏移索引表实现子图块替换
  - 基于地图画布的居中渲染
  - 支持大地图（已验证 840 张图、17MB+ 文件）
- **导出** — WAS 帧导出为 PNG，动画导出为 GIF，地图导出为 PNG

## 支持的文件格式

| 格式 | 描述 | 版本 |
|------|------|------|
| `.was` | 精灵动画（索引调色板 + RLE 压缩） | WAS v1 / v2 |
| `.wdf` | WAS 文件归档容器 | v1 |
| `.map` | 场景/地图（内嵌 JPEG 图块） | Format 1 (1GNP) / Format 2 (GEPJ/2GPJ) |

## 环境依赖

- Python 3.8+
- Pillow
- OpenCV (cv2)
- NumPy

## 安装

```bash
pip install -r requirements.txt
```

## 使用方法

### 启动 GUI 界面

```bash
python was_ui.py
```

### 编程接口

```python
# 解析 WAS 文件
from was_parser import load_was
was = load_was("sprite.was")

# 解析 MAP 文件
from map_parser import load_map
mf = load_map("scene.map")

# 渲染 MAP 为图片
from map_renderer import render_map
img = render_map("scene.map")
img.save("scene.png")

# 导出 WAS 帧为 PNG/GIF
from was_viewer import export_frame_png, export_gif
export_frame_png(was, 0, 0, "frame0.png")
export_gif(was, "anim.gif")
```

## 文件结构

```
├── was_parser.py      # WAS 精灵解析器
├── wdf_parser.py      # WDF 归档解析器
├── was_viewer.py      # WAS 渲染和导出工具
├── was_ui.py          # Tkinter GUI 主程序
├── map_parser.py      # MAP 文件解析器
├── map_renderer.py    # MAP 渲染引擎
├── README.md          # 英文说明
├── README_zh.md       # 中文说明
├── LICENSE            # MIT 许可证
├── requirements.txt   # 依赖清单
└── screenshot.png     # 界面截图
```

## 许可证

MIT
