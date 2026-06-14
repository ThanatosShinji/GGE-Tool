#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# MIT License - see LICENSE file in root directory
"""
WAS/WDF 素材查看器 - tkinter UI
支持:
  - 打开 WAS / WDF 文件
  - 显示静态帧预览
  - 动态图动画播放
  - WDF 文件内部浏览
"""

import os
import sys
import threading
import time

# 添加当前目录到路径
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import tkinter as tk
from tkinter import ttk, filedialog, messagebox
from PIL import Image, ImageTk

from was_parser import load_was, load_was_from_bytes, WasImage, WasFrame
from wdf_parser import load_wdf, WdfFile, WasFileNode
from map_parser import load_map, load_map_from_bytes, MapFile, MapSubArea
from map_renderer import render_map
from was_viewer import (export_frame_png, export_gif)


# ============================================================
# 图像渲染工具
# ============================================================

def _get_was_bounds(was: WasImage):
    """
    计算 WAS 所有帧在画布上的实际包围盒
    返回 (x_min, y_min, x_max, y_max) 相对于 was 中心点对齐后的坐标
    """
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
    """已移到 was_viewer.py，保留别名避免破坏已有导入"""
    from was_viewer import render_frame_to_pil as _impl
    return _impl(was, frame, scale)


def render_frame_to_photo(was: WasImage, frame: WasFrame, max_size: int = 512) -> ImageTk.PhotoImage:
    """已移到 was_viewer.py，保留别名避免破坏已有导入"""
    from was_viewer import render_frame_to_photo as _impl
    return _impl(was, frame, max_size)


# ============================================================
# WAS 预览面板
# ============================================================

class WasPreviewPanel(ttk.Frame):
    """WAS 文件预览面板 (支持动画播放)"""

    def __init__(self, parent, was: WasImage, title: str = ""):
        super().__init__(parent)
        self.was = was
        self.title = title
        self._anim_running = False
        self._anim_id = None
        self._current_sprite = 0
        self._current_frame = 0
        self._tk_images = {}  # 缓存 PhotoImage 防止被GC

        self._build_ui()
        self._load_frames()

    def _build_ui(self):
        # 标题
        title_text = self.title or f"WAS {self.was.width}x{self.was.height}"
        lbl_title = ttk.Label(self, text=title_text, font=("", 10, "bold"))
        lbl_title.pack(pady=(0, 4))

        # 信息栏
        info_text = (f"精灵={self.was.sprite_count} 帧={self.was.frame_count} "
                     f"大小={self.was.width}x{self.was.height} "
                     f"中心=({self.was.x_center},{self.was.y_center})")
        lbl_info = ttk.Label(self, text=info_text, font=("", 8))
        lbl_info.pack(pady=(0, 4))

        # 画布 (根据实际包围盒大小自适应)
        bx0, by0, bx1, by1 = _get_was_bounds(self.was)
        bw, bh = bx1 - bx0, by1 - by0
        display_size = min(max(bw, bh, 200), 512)
        self.canvas = tk.Canvas(self, width=display_size, height=display_size,
                                bg="#222222",
                                highlightthickness=1, highlightbackground="#555555")
        self.canvas.pack(padx=4, pady=4)

        # 控制栏
        ctrl_frame = ttk.Frame(self)
        ctrl_frame.pack(fill=tk.X, padx=4, pady=2)

        # 精灵选择
        if self.was.sprite_count > 1:
            ttk.Label(ctrl_frame, text="精灵:").pack(side=tk.LEFT, padx=(0, 2))
            self.sprite_var = tk.IntVar(value=0)
            self.sprite_combo = ttk.Combobox(ctrl_frame, textvariable=self.sprite_var,
                                              values=list(range(self.was.sprite_count)),
                                              width=4, state="readonly")
            self.sprite_combo.pack(side=tk.LEFT, padx=(0, 8))
            self.sprite_combo.bind("<<ComboboxSelected>>", self._on_sprite_change)

        # 帧导航
        self.btn_first = ttk.Button(ctrl_frame, text="⏮", width=3, command=self._go_first)
        self.btn_first.pack(side=tk.LEFT, padx=1)

        self.btn_prev = ttk.Button(ctrl_frame, text="◀", width=3, command=self._go_prev)
        self.btn_prev.pack(side=tk.LEFT, padx=1)

        self.frame_label = ttk.Label(ctrl_frame, text="0/0", width=8, anchor=tk.CENTER)
        self.frame_label.pack(side=tk.LEFT, padx=2)

        self.btn_next = ttk.Button(ctrl_frame, text="▶", width=3, command=self._go_next)
        self.btn_next.pack(side=tk.LEFT, padx=1)

        self.btn_last = ttk.Button(ctrl_frame, text="⏭", width=3, command=self._go_last)
        self.btn_last.pack(side=tk.LEFT, padx=1)

        # 播放/停止
        self.btn_play = ttk.Button(ctrl_frame, text="▶ 播放", command=self._toggle_play)
        self.btn_play.pack(side=tk.LEFT, padx=(8, 1))

        # 导出按钮
        ttk.Separator(ctrl_frame, orient=tk.VERTICAL).pack(side=tk.LEFT, fill=tk.Y, padx=4)
        ttk.Label(ctrl_frame, text="导出:").pack(side=tk.LEFT, padx=(0, 2))
        btn_export_png = ttk.Button(ctrl_frame, text="PNG", width=4, command=self._export_png)
        btn_export_png.pack(side=tk.LEFT, padx=1)
        btn_export_gif = ttk.Button(ctrl_frame, text="GIF", width=4, command=self._export_gif)
        btn_export_gif.pack(side=tk.LEFT, padx=(1, 4))

        # 帧延时信息
        self.delay_label = ttk.Label(ctrl_frame, text="", font=("", 7))
        self.delay_label.pack(side=tk.RIGHT, padx=2)

        # 帧序号输入
        ttk.Label(ctrl_frame, text="跳转:").pack(side=tk.RIGHT, padx=(0, 2))
        self.jump_var = tk.StringVar()
        self.jump_entry = ttk.Entry(ctrl_frame, textvariable=self.jump_var, width=5)
        self.jump_entry.pack(side=tk.RIGHT, padx=1)
        self.jump_entry.bind("<Return>", self._on_jump)

    def _load_frames(self):
        """预渲染所有帧到 PhotoImage 缓存"""
        self._tk_images = {}
        total = self.was.sprite_count * self.was.frame_count
        if total == 0:
            return

        for s in range(self.was.sprite_count):
            for f in range(self.was.frame_count):
                frame = self.was.get_frame(s, f)
                if frame:
                    try:
                        photo = render_frame_to_photo(self.was, frame)
                        self._tk_images[(s, f)] = photo
                    except Exception as e:
                        print(f"  [!] 渲染帧 [{s}][{f}] 失败: {e}")

        self._show_frame(0, 0)
        # 布局完成后重新居中
        self.after(50, lambda: self._show_frame(self._current_sprite, self._current_frame))

    def _show_frame(self, sprite_idx: int, frame_idx: int):
        """显示指定帧"""
        self._current_sprite = sprite_idx
        self._current_frame = frame_idx

        total_frames = self.was.frame_count
        self.frame_label.config(text=f"{frame_idx + 1}/{total_frames}")

        # 更新延时信息
        frame = self.was.get_frame(sprite_idx, frame_idx)
        if frame:
            self.delay_label.config(text=f"delay={frame.delay}")

        # 显示图像 (用 Canvas 配置大小居中，避免布局未完成时 winfo 返回 0)
        photo = self._tk_images.get((sprite_idx, frame_idx))
        if photo:
            self.canvas.delete("all")
            cw = self.canvas.winfo_width()
            ch = self.canvas.winfo_height()
            if cw < 10:
                cw = int(self.canvas.cget("width"))
            if ch < 10:
                ch = int(self.canvas.cget("height"))
            cx = cw // 2
            cy = ch // 2
            self.canvas.create_image(cx, cy, image=photo, anchor=tk.CENTER)
            self.canvas.image = photo  # 防止GC

    def _go_first(self):
        self._stop_anim()
        self._show_frame(self._current_sprite, 0)

    def _go_prev(self):
        self._stop_anim()
        f = max(0, self._current_frame - 1)
        self._show_frame(self._current_sprite, f)

    def _go_next(self):
        self._stop_anim()
        f = min(self.was.frame_count - 1, self._current_frame + 1)
        self._show_frame(self._current_sprite, f)

    def _go_last(self):
        self._stop_anim()
        self._show_frame(self._current_sprite, self.was.frame_count - 1)

    def _on_sprite_change(self, event=None):
        self._stop_anim()
        self._show_frame(self.sprite_var.get(), 0)

    def _on_jump(self, event=None):
        self._stop_anim()
        try:
            idx = int(self.jump_var.get()) - 1
            if 0 <= idx < self.was.frame_count:
                self._show_frame(self._current_sprite, idx)
        except ValueError:
            pass

    def _toggle_play(self):
        if self._anim_running:
            self._stop_anim()
        else:
            self._start_anim()

    def _start_anim(self):
        if self._anim_running:
            return
        self._anim_running = True
        self.btn_play.config(text="■ 停止")
        self._anim_step()

    def _stop_anim(self):
        self._anim_running = False
        self.btn_play.config(text="▶ 播放")
        if self._anim_id:
            self.after_cancel(self._anim_id)
            self._anim_id = None

    def _anim_step(self):
        if not self._anim_running:
            return

        # 下一帧
        next_frame = self._current_frame + 1
        if next_frame >= self.was.frame_count:
            next_frame = 0

        self._show_frame(self._current_sprite, next_frame)

        # 计算延时 (Java中delay是帧数, 这里按 ~50ms/帧 估算)
        frame = self.was.get_frame(self._current_sprite, self._current_frame)
        delay_ms = max(50, (frame.delay if frame else 1) * 30)

        self._anim_id = self.after(delay_ms, self._anim_step)

    def _export_png(self):
        """导出当前帧为PNG"""
        path = filedialog.asksaveasfilename(
            title="导出当前帧为PNG",
            defaultextension=".png",
            filetypes=[("PNG 图片", "*.png")],
            initialdir="."
        )
        if not path:
            return
        try:
            export_frame_png(self.was, self._current_sprite, self._current_frame, path, scale=1)
            messagebox.showinfo("导出成功", f"已保存: {path}")
        except Exception as e:
            messagebox.showerror("导出失败", f"PNG 导出失败:\n{e}")

    def _export_gif(self):
        """导出所有帧为GIF动画"""
        path = filedialog.asksaveasfilename(
            title="导出所有帧为GIF",
            defaultextension=".gif",
            filetypes=[("GIF 动画", "*.gif")],
            initialdir="."
        )
        if not path:
            return
        try:
            # 计算每帧持续时间：取当前精灵帧 delay 的平均值 * 30ms
            total_delay = 0
            valid_frames = 0
            for n in range(self.was.frame_count):
                f = self.was.get_frame(self._current_sprite, n)
                if f:
                    total_delay += max(1, f.delay) * 30
                    valid_frames += 1
            duration = max(50, total_delay // valid_frames) if valid_frames else 100
            export_gif(self.was, path, scale=1, loop=0, duration=duration,
                       sprite_idx=self._current_sprite)
            messagebox.showinfo("导出成功", f"已保存: {path}")
        except Exception as e:
            messagebox.showerror("导出失败", f"GIF 导出失败:\n{e}")

    def destroy(self):
        self._stop_anim()
        super().destroy()


# ============================================================
# 主窗口
# ============================================================

class WasViewerApp:
    """WAS/WDF 素材查看器主窗口"""

    def __init__(self):
        self.root = tk.Tk()
        self.root.title("WAS/WDF 素材查看器")
        self.root.geometry("900x650")
        self.root.minsize(700, 500)

        # 当前打开的文件信息
        self.current_wdf: WdfFile = None
        self.current_was: WasImage = None
        self.current_map: MapFile = None
        self.current_filepath: str = ""
        self.current_mode: str = ""  # "was" or "wdf" or "map"

        # 预览面板引用
        self.preview_panel: WasPreviewPanel = None

        self._build_menu()
        self._build_ui()
        self._bind_shortcuts()

    def _build_menu(self):
        menubar = tk.Menu(self.root)
        self.root.config(menu=menubar)

        file_menu = tk.Menu(menubar, tearoff=0)
        menubar.add_cascade(label="文件", menu=file_menu)
        file_menu.add_command(label="打开 WAS 文件...", command=self._open_was, accelerator="Ctrl+W")
        file_menu.add_command(label="打开 WDF 文件...", command=self._open_wdf, accelerator="Ctrl+D")
        file_menu.add_command(label="打开 MAP 文件...", command=self._open_map, accelerator="Ctrl+M")
        file_menu.add_separator()
        file_menu.add_command(label="退出", command=self.root.quit, accelerator="Ctrl+Q")

        view_menu = tk.Menu(menubar, tearoff=0)
        menubar.add_cascade(label="视图", menu=view_menu)
        view_menu.add_command(label="适应窗口", command=self._fit_view)

        help_menu = tk.Menu(menubar, tearoff=0)
        menubar.add_cascade(label="帮助", menu=help_menu)
        help_menu.add_command(label="关于", command=self._show_about)

    def _build_ui(self):
        # 主布局: 左侧文件列表 (WDF), 右侧预览
        self.main_pw = ttk.PanedWindow(self.root, orient=tk.HORIZONTAL)
        self.main_pw.pack(fill=tk.BOTH, expand=True, padx=4, pady=4)

        # 左侧面板 (WDF 文件列表)
        self.left_frame = ttk.Frame(self.main_pw, width=280)
        self.main_pw.add(self.left_frame, weight=0)

        # 文件列表标题
        lbl_header = ttk.Label(self.left_frame, text="文件列表", font=("", 10, "bold"))
        lbl_header.pack(fill=tk.X, padx=4, pady=(4, 2))

        # 文件列表
        list_frame = ttk.Frame(self.left_frame)
        list_frame.pack(fill=tk.BOTH, expand=True, padx=4)

        self.file_listbox = tk.Listbox(list_frame, font=("Consolas", 9),
                                       selectmode=tk.SINGLE, bg="#1e1e1e", fg="#d4d4d4",
                                       selectbackground="#094771")
        self.file_listbox.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

        scrollbar = ttk.Scrollbar(list_frame, orient=tk.VERTICAL, command=self.file_listbox.yview)
        scrollbar.pack(side=tk.RIGHT, fill=tk.Y)
        self.file_listbox.config(yscrollcommand=scrollbar.set)
        self.file_listbox.bind("<<ListboxSelect>>", self._on_file_select)

        # 文件信息标签
        self.file_info_label = ttk.Label(self.left_frame, text="", font=("", 8),
                                         foreground="#888888", wraplength=260)
        self.file_info_label.pack(fill=tk.X, padx=4, pady=(2, 4))

        # 右侧预览面板容器
        self.right_frame = ttk.Frame(self.main_pw)
        self.main_pw.add(self.right_frame, weight=1)

        # 占位提示
        self.placeholder = ttk.Label(self.right_frame,
                                      text="打开 WAS 或 WDF 文件以预览\n\n"
                                           "快捷键:\n"
                                           "  Ctrl+W  打开 WAS\n"
                                           "  Ctrl+D  打开 WDF\n"
                                           "  空格     播放/暂停",
                                      font=("", 12), foreground="#888888",
                                      anchor=tk.CENTER, justify=tk.CENTER)
        self.placeholder.pack(fill=tk.BOTH, expand=True)

        # 状态栏
        self.status_bar = ttk.Label(self.root, text="就绪", relief=tk.SUNKEN,
                                    anchor=tk.W, font=("", 8))
        self.status_bar.pack(fill=tk.X, side=tk.BOTTOM, padx=2, pady=1)

    def _bind_shortcuts(self):
        self.root.bind("<Control-w>", lambda e: self._open_was())
        self.root.bind("<Control-d>", lambda e: self._open_wdf())
        self.root.bind("<Control-m>", lambda e: self._open_map())
        self.root.bind("<Control-q>", lambda e: self.root.quit())
        self.root.bind("<space>", lambda e: self._toggle_play())
        self.root.bind("<Left>", lambda e: self._prev_frame())
        self.root.bind("<Right>", lambda e: self._next_frame())

    def _set_status(self, text: str):
        self.status_bar.config(text=text)
        self.root.update_idletasks()

    # ---- 打开文件 ----

    def _open_was(self):
        path = filedialog.askopenfilename(
            title="打开 WAS 文件",
            filetypes=[("WAS 文件", "*.was"), ("所有文件", "*.*")],
            initialdir=os.path.dirname(self.current_filepath) if self.current_filepath else "."
        )
        if not path:
            return
        self._load_was_file(path)

    def _open_wdf(self):
        path = filedialog.askopenfilename(
            title="打开 WDF 文件",
            filetypes=[("WDF 文件", "*.wdf"), ("所有文件", "*.*")],
            initialdir=os.path.dirname(self.current_filepath) if self.current_filepath else "."
        )
        if not path:
            return
        self._load_wdf_file(path)

    def _open_map(self):
        path = filedialog.askopenfilename(
            title="打开 MAP 文件",
            filetypes=[("MAP 文件", "*.map"), ("所有文件", "*.*")],
            initialdir=os.path.dirname(self.current_filepath) if self.current_filepath else "."
        )
        if not path:
            return
        self._load_map_file(path)

    def _load_map_file(self, path: str):
        """加载并显示 MAP 文件（后台线程，不阻塞UI）"""
        self._set_status(f"加载中: {os.path.basename(path)}...")

        def _do_load():
            try:
                mf = load_map(path)
                self.root.after(0, lambda: self._on_map_loaded(mf, path))
            except Exception as e:
                self.root.after(0, lambda: self._on_map_error(e, path))

        threading.Thread(target=_do_load, daemon=True).start()

    def _on_map_loaded(self, mf, path):
        """load_map完成后在主线程更新UI"""
        self.current_filepath = path
        self.current_mode = "map"
        self.current_map = mf
        self.current_was = None
        self.current_wdf = None

        # 隐藏占位, 显示预览
        self.placeholder.pack_forget()
        self._show_map_preview(mf, os.path.basename(path))

        # 清空文件列表
        self.file_listbox.delete(0, tk.END)
        self.file_info_label.config(text="")

        self._set_status(f"已加载 MAP: {os.path.basename(path)} "
                         f"({mf.map_width}x{mf.map_height}, {len(mf.all_tile_ids)}图块, {len(mf.images)}图像)")

    def _on_map_error(self, e, path):
        """加载失败时显示错误"""
        self._set_status("加载失败")
        messagebox.showerror("加载失败", f"无法加载 MAP 文件:\n{e}")

    def _load_was_file(self, path: str):
        """加载并显示 WAS 文件"""
        try:
            self._set_status(f"加载中: {os.path.basename(path)}")
            was = load_was(path)
            self.current_filepath = path
            self.current_mode = "was"
            self.current_was = was
            self.current_wdf = None

            # 隐藏占位, 显示预览
            self.placeholder.pack_forget()
            self._show_was_preview(was, os.path.basename(path))

            # 清空文件列表
            self.file_listbox.delete(0, tk.END)
            self.file_info_label.config(text="")

            self._set_status(f"已加载 WAS: {os.path.basename(path)} "
                             f"({was.width}x{was.height}, {was.frame_count}帧)")
        except Exception as e:
            messagebox.showerror("加载失败", f"无法加载 WAS 文件:\n{e}")
            self._set_status("加载失败")

    def _load_wdf_file(self, path: str):
        """加载 WDF 文件并显示文件列表"""
        try:
            self._set_status(f"加载中: {os.path.basename(path)}")
            wdf = load_wdf(path)
            self.current_filepath = path
            self.current_mode = "wdf"
            self.current_wdf = wdf
            self.current_was = None

            # 隐藏占位
            self.placeholder.pack_forget()

            # 清除旧预览
            if self.preview_panel:
                self.preview_panel.destroy()
                self.preview_panel = None

            # 填充文件列表
            self.file_listbox.delete(0, tk.END)
            for node in wdf.get_file_list_sorted("id"):
                name_str = node.name if node.name else ""
                display = f"0x{node.id:08X}  {name_str}"
                self.file_listbox.insert(tk.END, display)

            self.file_info_label.config(text=f"共 {wdf.file_count} 个文件  |  "
                                             f"{os.path.getsize(path):,} bytes")

            # 在右侧显示 WDF 信息
            self._show_wdf_info(wdf)

            self._set_status(f"已加载 WDF: {os.path.basename(path)} ({wdf.file_count} 个文件)")
        except Exception as e:
            messagebox.showerror("加载失败", f"无法加载 WDF 文件:\n{e}")
            self._set_status("加载失败")

    # ---- 显示 ----

    def _show_was_preview(self, was: WasImage, title: str = ""):
        """在右侧显示 WAS 预览"""
        if self.preview_panel:
            self.preview_panel.destroy()

        self.preview_panel = WasPreviewPanel(self.right_frame, was, title)
        self.preview_panel.pack(fill=tk.BOTH, expand=True, padx=4, pady=4)

    def _show_map_preview(self, mf: MapFile, title: str = ""):
        """在右侧显示 MAP 预览"""
        if self.preview_panel:
            self.preview_panel.destroy()

        frame = ttk.Frame(self.right_frame)
        frame.pack(fill=tk.BOTH, expand=True, padx=4, pady=4)

        # 使用 Notebook 分页: 信息 / 图块统计 / 图像
        notebook = ttk.Notebook(frame)
        notebook.pack(fill=tk.BOTH, expand=True)

        # ---- 信息页 ----
        info_frame = ttk.Frame(notebook)
        notebook.add(info_frame, text="信息")

        text = tk.Text(info_frame, font=("Consolas", 10), bg="#1e1e1e", fg="#d4d4d4",
                       wrap=tk.NONE, state=tk.DISABLED)
        text.pack(fill=tk.BOTH, expand=True)

        scroll_y = ttk.Scrollbar(info_frame, orient=tk.VERTICAL, command=text.yview)
        scroll_y.pack(side=tk.RIGHT, fill=tk.Y)
        text.config(yscrollcommand=scroll_y.set)

        scroll_x = ttk.Scrollbar(info_frame, orient=tk.HORIZONTAL, command=text.xview)
        scroll_x.pack(side=tk.BOTTOM, fill=tk.X)
        text.config(xscrollcommand=scroll_x.set)

        # 先显示基本信息（不含耗时统计）
        content = []
        content.append(f"MAP 文件: {title}\n")
        # 安全获取文件大小
        try:
            fsize = os.path.getsize(mf.filepath)
            content.append(f"文件大小: {fsize:,} bytes\n")
        except (OSError, ValueError):
            content.append(f"文件大小: (内存加载)\n")
        content.append(f"地图尺寸: {mf.map_width} x {mf.map_height}\n")
        content.append(f"版本: {mf.version or '0.1M'}\n")
        content.append(f"总对象数: {mf.total_object_count}\n")
        content.append(f"区块数: {mf.block_count}\n")
        content.append(f"子区域数: {mf.sub_area_count}\n")
        content.append(f"内嵌图像数: {len(mf.images)}\n")
        content.append(f"格式版本: v{mf.format_version}\n")
        content.append(f"\n{'='*60}\n")
        content.append(f"子区域详情:\n")
        content.append(f"{'-'*60}\n")
        for sa in mf.sub_areas:
            content.append(f"  区域{sa.index}: {len(sa.tile_ids)}图块, "
                          f"范围=[{sa.field2}x{sa.field3}]\n")

        content.append(f"\n{'='*60}\n")
        content.append(f"内嵌图像:\n")
        content.append(f"{'-'*60}\n")
        for img in mf.images:
            content.append(f"  图像{img.index}: {img.width}x{img.height}, "
                          f"{len(img.jpeg_data)} bytes\n")

        text.config(state=tk.NORMAL)
        text.insert(tk.END, "".join(content))
        text.config(state=tk.DISABLED)

        # ---- 图块统计页（延迟加载，避免卡UI） ----
        tiles_frame = ttk.Frame(notebook)
        notebook.add(tiles_frame, text="图块统计")

        text2 = tk.Text(tiles_frame, font=("Consolas", 9), bg="#1e1e1e", fg="#d4d4d4",
                        wrap=tk.NONE)
        text2.pack(fill=tk.BOTH, expand=True)

        scroll_y2 = ttk.Scrollbar(tiles_frame, orient=tk.VERTICAL, command=text2.yview)
        scroll_y2.pack(side=tk.RIGHT, fill=tk.Y)
        text2.config(yscrollcommand=scroll_y2.set)

        # 显示加载提示，后台线程计算统计
        text2.insert(tk.END, "正在计算图块统计...\n")
        text2.config(state=tk.DISABLED)

        def _calc_stats():
            """后台计算图块统计"""
            from collections import Counter
            total = len(mf.all_tile_ids)
            unique = len(set(mf.all_tile_ids)) if mf.all_tile_ids else 0
            counter = Counter(mf.all_tile_ids) if mf.all_tile_ids else Counter()
            lines = [f"总图块数: {total:,}\n"]
            lines.append(f"不同图块ID数: {unique}\n")
            if mf.all_tile_ids:
                lines.append(f"图块ID范围: {min(mf.all_tile_ids)} - {max(mf.all_tile_ids)}\n")
            else:
                lines.append("图块ID范围: (无RLE图块数据)\n")
            lines.append(f"\n{'图块ID':>8}  {'出现次数':>10}  {'占比':>8}\n")
            lines.append(f"{'-'*8}  {'-'*10}  {'-'*8}\n")
            for tile_id, count in counter.most_common(50):
                pct = count / total * 100
                lines.append(f"{tile_id:>8}  {count:>10}  {pct:>7.2f}%\n")
            return "".join(lines)

        def _update_stats():
            result = _calc_stats()
            text2.config(state=tk.NORMAL)
            text2.delete("1.0", tk.END)
            text2.insert(tk.END, result)
            text2.config(state=tk.DISABLED)

        # 延迟100ms后计算，让UI先渲染完成
        frame.after(100, _update_stats)

        # ---- 图像页 ----
        if mf.images:
            img_frame = ttk.Frame(notebook)
            notebook.add(img_frame, text="图像")

            img_canvas_frame = ttk.Frame(img_frame)
            img_canvas_frame.pack(fill=tk.BOTH, expand=True)

            img_canvas = tk.Canvas(img_canvas_frame, bg="#222222")
            img_canvas.pack(fill=tk.BOTH, expand=True)

            # 显示第一张图像（GEPJ需先修复）
            try:
                from PIL import Image, ImageTk
                import io
                from map_parser import MapParser
                jpeg_data = mf.images[0].jpeg_data
                if jpeg_data[2:4] == b'\xff\xa0':
                    jpeg_data = MapParser._fix_gepj_jpeg(jpeg_data)
                pil_img = Image.open(io.BytesIO(jpeg_data))
                max_w, max_h = 400, 300
                if pil_img.width > max_w or pil_img.height > max_h:
                    ratio = min(max_w / pil_img.width, max_h / pil_img.height)
                    pil_img = pil_img.resize((int(pil_img.width * ratio),
                                              int(pil_img.height * ratio)), Image.LANCZOS)
                photo = ImageTk.PhotoImage(pil_img)
                img_canvas.create_image(10, 10, image=photo, anchor=tk.NW)
                img_canvas.image = photo

                img_sel_frame = ttk.Frame(img_frame)
                img_sel_frame.pack(fill=tk.X, padx=4, pady=2)

                ttk.Label(img_sel_frame, text="选择图像:").pack(side=tk.LEFT, padx=2)
                img_var = tk.IntVar(value=0)
                img_combo = ttk.Combobox(img_sel_frame, textvariable=img_var,
                                         values=list(range(len(mf.images))),
                                         width=4, state="readonly")
                img_combo.pack(side=tk.LEFT, padx=2)

                img_label = ttk.Label(img_sel_frame, text=f"{mf.images[0].width}x{mf.images[0].height}")
                img_label.pack(side=tk.LEFT, padx=4)

                def on_img_select(event=None):
                    idx = img_var.get()
                    if 0 <= idx < len(mf.images):
                        img_data = mf.images[idx]
                        jpeg_data = img_data.jpeg_data
                        if jpeg_data[2:4] == b'\xff\xa0':
                            jpeg_data = MapParser._fix_gepj_jpeg(jpeg_data)
                        pil = Image.open(io.BytesIO(jpeg_data))
                        max_w, max_h = 400, 300
                        if pil.width > max_w or pil.height > max_h:
                            ratio = min(max_w / pil.width, max_h / pil.height)
                            pil = pil.resize((int(pil.width * ratio),
                                              int(pil.height * ratio)), Image.LANCZOS)
                        ph = ImageTk.PhotoImage(pil)
                        img_canvas.delete("all")
                        img_canvas.create_image(10, 10, image=ph, anchor=tk.NW)
                        img_canvas.image = ph
                        img_label.config(text=f"{img_data.width}x{img_data.height}")

                img_combo.bind("<<ComboboxSelected>>", on_img_select)
            except Exception as e:
                ttk.Label(img_frame, text=f"无法显示图像: {e}").pack(padx=10, pady=10)

        # ---- 渲染地图页 ----
        map_frame = ttk.Frame(notebook)
        notebook.add(map_frame, text="渲染地图")

        # 工具栏
        toolbar = ttk.Frame(map_frame)
        toolbar.pack(fill=tk.X, padx=4, pady=2)

        ttk.Label(toolbar, text=f"地图 {mf.map_width}x{mf.map_height}").pack(side=tk.LEFT, padx=2)

        tile_size_var = tk.IntVar(value=2)
        ttk.Label(toolbar, text="格子像素:").pack(side=tk.LEFT, padx=(8, 2))
        ts_combo = ttk.Combobox(toolbar, textvariable=tile_size_var,
                                 values=[1, 2, 3, 4, 6, 8], width=3, state="readonly")
        ts_combo.pack(side=tk.LEFT, padx=2)

        render_btn = ttk.Button(toolbar, text="渲染地图", command=None)
        render_btn.pack(side=tk.LEFT, padx=8)

        ttk.Separator(toolbar, orient=tk.VERTICAL).pack(side=tk.LEFT, fill=tk.Y, padx=4)
        ttk.Label(toolbar, text="导出:").pack(side=tk.LEFT, padx=(0, 2))
        export_png_btn = ttk.Button(toolbar, text="PNG", width=4, command=None)
        export_png_btn.pack(side=tk.LEFT, padx=1)

        status_label = ttk.Label(toolbar, text="就绪", foreground="#888", font=("", 8))
        status_label.pack(side=tk.RIGHT, padx=4)

        # 画布 + 滚动条
        canvas_frame = ttk.Frame(map_frame)
        canvas_frame.pack(fill=tk.BOTH, expand=True)

        hbar = ttk.Scrollbar(canvas_frame, orient=tk.HORIZONTAL)
        vbar = ttk.Scrollbar(canvas_frame, orient=tk.VERTICAL)
        map_canvas = tk.Canvas(canvas_frame, bg="#222222",
                                xscrollcommand=hbar.set,
                                yscrollcommand=vbar.set)
        hbar.config(command=map_canvas.xview)
        vbar.config(command=map_canvas.yview)

        map_canvas.grid(row=0, column=0, sticky="nsew")
        vbar.grid(row=0, column=1, sticky="ns")
        hbar.grid(row=1, column=0, sticky="ew")
        canvas_frame.grid_rowconfigure(0, weight=1)
        canvas_frame.grid_columnconfigure(0, weight=1)

        render_state = {"photo": None, "pil": None, "rendering": False}

        def _do_render(ts):
            """后台渲染"""
            nonlocal render_state
            if render_state["rendering"]:
                return
            render_state["rendering"] = True
            status_label.config(text="渲染中...")
            render_btn.config(state=tk.DISABLED)

            def _render_task():
                try:
                    pil_img = render_map(None, tile_size=ts, mf=mf)
                    # 缩放以适应屏幕
                    max_display = 800
                    ow, oh = pil_img.size
                    if ow > max_display or oh > max_display:
                        ratio = min(max_display / ow, max_display / oh)
                        nw = int(ow * ratio)
                        nh = int(oh * ratio)
                        display = pil_img.resize((nw, nh), Image.NEAREST)
                    else:
                        display = pil_img
                    render_state["pil"] = pil_img
                    return display
                except Exception as e:
                    return f"ERROR: {e}"

            def _on_done(result):
                nonlocal render_state
                render_state["rendering"] = False
                render_btn.config(state=tk.NORMAL)
                if isinstance(result, str):
                    status_label.config(text=f"渲染失败", foreground="red")
                    map_canvas.delete("all")
                    map_canvas.create_text(200, 100, text=result, fill="red", font=("", 10))
                    return
                try:
                    photo = ImageTk.PhotoImage(result)
                    render_state["photo"] = photo
                    map_canvas.delete("all")
                    w, h = result.size
                    map_canvas.config(scrollregion=(0, 0, w, h))
                    map_canvas.create_image(0, 0, image=photo, anchor=tk.NW)
                    status_label.config(text=f"{w}x{h} ({len(mf.all_tile_ids)} tiles)", foreground="#888")
                except Exception as e:
                    status_label.config(text=f"显示失败: {e}", foreground="red")

            def _run():
                result = _render_task()
                map_canvas.after(0, lambda: _on_done(result))

            threading.Thread(target=_run, daemon=True).start()

        def _on_render():
            ts = tile_size_var.get()
            _do_render(ts)

        def _on_export_png():
            """导出渲染结果为PNG"""
            pil_img = render_state.get("pil")
            if pil_img is None:
                # 先渲染再导出
                ts = tile_size_var.get()
                try:
                    pil_img = render_map(None, tile_size=ts, mf=mf)
                except Exception as e:
                    messagebox.showerror("导出失败", f"渲染失败:\n{e}")
                    return
            path = filedialog.asksaveasfilename(
                title="导出地图为PNG",
                defaultextension=".png",
                filetypes=[("PNG 图片", "*.png")],
                initialdir="."
            )
            if not path:
                return
            try:
                pil_img.save(path)
                messagebox.showinfo("导出成功", f"已保存: {path}")
                status_label.config(text=f"已导出: {os.path.basename(path)}")
            except Exception as e:
                messagebox.showerror("导出失败", f"PNG 导出失败:\n{e}")

        render_btn.config(command=_on_render)
        export_png_btn.config(command=_on_export_png)

        # 鼠标滚轮绑定
        def _on_mousewheel(event):
            if event.delta:
                map_canvas.xview_scroll(int(-1 * (event.delta / 120)), "units")
        map_canvas.bind("<MouseWheel>", _on_mousewheel)

        # 加载时自动渲染
        frame.after(200, lambda: _do_render(2))

        self.preview_panel = frame

    def _show_wdf_info(self, wdf: WdfFile):
        """在右侧显示 WDF 信息"""
        if self.preview_panel:
            self.preview_panel.destroy()

        frame = ttk.Frame(self.right_frame)
        frame.pack(fill=tk.BOTH, expand=True, padx=4, pady=4)

        text = tk.Text(frame, font=("Consolas", 10), bg="#1e1e1e", fg="#d4d4d4",
                       wrap=tk.NONE, state=tk.DISABLED)
        text.pack(fill=tk.BOTH, expand=True)

        scroll_y = ttk.Scrollbar(frame, orient=tk.VERTICAL, command=text.yview)
        scroll_y.pack(side=tk.RIGHT, fill=tk.Y)
        text.config(yscrollcommand=scroll_y.set)

        scroll_x = ttk.Scrollbar(frame, orient=tk.HORIZONTAL, command=text.xview)
        scroll_x.pack(side=tk.BOTTOM, fill=tk.X)
        text.config(xscrollcommand=scroll_x.set)

        # 填充内容
        content = []
        content.append(f"WDF 文件: {os.path.basename(wdf.filepath)}\n")
        content.append(f"文件数: {wdf.file_count}\n")
        content.append(f"文件大小: {os.path.getsize(wdf.filepath):,} bytes\n")
        content.append(f"\n{'ID':>10}  {'别名':<20}  {'偏移':>8}  {'大小':>6}\n")
        content.append(f"{'-'*10}  {'-'*20}  {'-'*8}  {'-'*6}\n")
        for node in wdf.get_file_list_sorted("id"):
            name = node.name if node.name else ""
            content.append(f"0x{node.id:08X}  {name:<20}  0x{node.offset:08X}  {node.size:>6}\n")

        text.config(state=tk.NORMAL)
        text.insert(tk.END, "".join(content))
        text.config(state=tk.DISABLED)

        self.preview_panel = frame

    def _on_file_select(self, event=None):
        """WDF 文件列表选择事件"""
        if not self.current_wdf:
            return

        selection = self.file_listbox.curselection()
        if not selection:
            return

        idx = selection[0]
        if idx >= len(self.current_wdf.file_list):
            return

        node = self.current_wdf.file_list[idx]
        self._preview_wdf_node(node)

    def _preview_wdf_node(self, node: WasFileNode):
        """预览 WDF 中的子文件"""
        try:
            data = self.current_wdf.get_file_data(node)
            if not data:
                return

            # 检测是否为 WAS 文件
            if len(data) >= 2 and data[:2] in (b"SP", b"SH"):
                was = load_was_from_bytes(data)
                name = node.name or node.id_hex
                self._show_was_preview(was, f"0x{node.id:08X} ({name})")
                self._set_status(f"预览: 0x{node.id:08X} ({name}) "
                                 f"{was.width}x{was.height} {was.frame_count}帧")
            # 检测是否为 MAP 文件
            elif len(data) >= 4 and data[:4] == b"0.1M":
                mf = load_map_from_bytes(data)
                mf.filepath = f"0x{node.id:08X}"
                self._show_map_preview(mf, f"0x{node.id:08X} ({node.name or ''})")
                self._set_status(f"预览 MAP: 0x{node.id:08X} ({mf.map_width}x{mf.map_height})")
            else:
                # 非 WAS 文件, 显示十六进制信息
                self._show_binary_info(node, data)
        except Exception as e:
            messagebox.showerror("预览失败", f"无法预览文件:\n{e}")

    def _show_binary_info(self, node: WasFileNode, data: bytes):
        """显示非WAS文件的二进制信息"""
        if self.preview_panel:
            self.preview_panel.destroy()

        frame = ttk.Frame(self.right_frame)
        frame.pack(fill=tk.BOTH, expand=True, padx=4, pady=4)

        text = tk.Text(frame, font=("Consolas", 9), bg="#1e1e1e", fg="#d4d4d4",
                       wrap=tk.NONE)
        text.pack(fill=tk.BOTH, expand=True)

        scroll_y = ttk.Scrollbar(frame, orient=tk.VERTICAL, command=text.yview)
        scroll_y.pack(side=tk.RIGHT, fill=tk.Y)
        text.config(yscrollcommand=scroll_y.set)

        scroll_x = ttk.Scrollbar(frame, orient=tk.HORIZONTAL, command=text.xview)
        scroll_x.pack(side=tk.BOTTOM, fill=tk.X)
        text.config(xscrollcommand=scroll_x.set)

        name = node.name or node.id_hex
        lines = [f"文件: 0x{node.id:08X} ({name})", f"大小: {node.size} bytes",
                 f"偏移: 0x{node.offset:08X}", f"标志: {data[:4]!r}", ""]

        # 十六进制转储 (前 512 bytes)
        dump_size = min(512, len(data))
        for i in range(0, dump_size, 16):
            hex_part = " ".join(f"{data[i+j]:02X}" for j in range(min(16, dump_size - i)))
            ascii_part = "".join(chr(data[i+j]) if 32 <= data[i+j] < 127 else "." for j in range(min(16, dump_size - i)))
            lines.append(f"{i:08X}  {hex_part:<48}  {ascii_part}")

        if len(data) > 512:
            lines.append(f"\n... 还有 {len(data) - 512} bytes")

        text.insert(tk.END, "\n".join(lines))
        text.config(state=tk.DISABLED)

        self.preview_panel = frame
        self._set_status(f"二进制预览: 0x{node.id:08X} ({node.size} bytes)")

    # ---- 快捷键操作 ----

    def _toggle_play(self):
        if self.preview_panel and hasattr(self.preview_panel, '_toggle_play'):
            self.preview_panel._toggle_play()

    def _prev_frame(self):
        if self.preview_panel and hasattr(self.preview_panel, '_go_prev'):
            self.preview_panel._go_prev()

    def _next_frame(self):
        if self.preview_panel and hasattr(self.preview_panel, '_go_next'):
            self.preview_panel._go_next()

    def _fit_view(self):
        """适应窗口 (重置画布大小)"""
        pass  # 自动适应

    def _show_about(self):
        messagebox.showinfo("关于", "WAS/WDF 素材查看器 v1.0\n\n"
                                    "基于 wastools (Java) 的 Python 实现\n"
                                    "支持 WAS 图像和 WDF 资源集合的预览")

    def run(self):
        self.root.mainloop()


# ============================================================
# 入口
# ============================================================

def main():
    # 支持命令行参数直接打开文件
    app = WasViewerApp()

    if len(sys.argv) > 1:
        path = sys.argv[1]
        if os.path.exists(path):
            with open(path, "rb") as f:
                magic = f.read(4)
            if magic[:2] in (b"SP", b"SH"):
                app._load_was_file(path)
            elif magic == b"PFDW":
                app._load_wdf_file(path)
            elif magic == b"0.1M":
                app._load_map_file(path)

    app.run()


if __name__ == "__main__":
    main()
