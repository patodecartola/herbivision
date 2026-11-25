import csv
import json
import math
import os
import subprocess
import sys
import tkinter as tk
from datetime import datetime
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

try:
    from PIL import Image, ImageTk, ImageDraw, ImageChops
except ImportError:
    raise SystemExit("Pillow não instalado. Rode: pip install Pillow")

VERTEX_RADIUS = 5
EDGE_HIT       = 7
VERTEX_HIT     = 9
ROT_OFFSET     = 28
RECT_COLOR     = "#ff2222"
VERTEX_FILL    = "#ffffff"
VERTEX_OUTL    = "#ff2222"
VERTEX_SEL     = "#ffd000"
ROT_COLOR      = "#22aaff"

def polygon_area(verts):
    n = len(verts); a = 0.0
    for i in range(n):
        x1, y1 = verts[i]; x2, y2 = verts[(i + 1) % n]
        a += x1 * y2 - x2 * y1
    return abs(a) / 2

def polygon_centroid(verts):
    if not verts: return (0, 0)
    sx = sum(p[0] for p in verts); sy = sum(p[1] for p in verts)
    return (sx / len(verts), sy / len(verts))

def point_in_polygon(px, py, verts):
    n = len(verts); inside = False; j = n - 1
    for i in range(n):
        xi, yi = verts[i]; xj, yj = verts[j]
        if (yi > py) != (yj > py):
            x_cross = (xj - xi) * (py - yi) / (yj - yi + 1e-12) + xi
            if px < x_cross: inside = not inside
        j = i
    return inside

def closest_point_on_segment(px, py, ax, ay, bx, by):
    dx, dy = bx - ax, by - ay
    L2 = dx * dx + dy * dy
    if L2 < 1e-9: return (ax, ay, 0.0, (px - ax) ** 2 + (py - ay) ** 2)
    t = max(0, min(1, ((px - ax) * dx + (py - ay) * dy) / L2))
    qx, qy = ax + t * dx, ay + t * dy
    return (qx, qy, t, (px - qx) ** 2 + (py - qy) ** 2)

class BrushPainter(tk.Toplevel):
    _PANEL_W = 240

    def __init__(self, master, image_path, title, instruction, ppcm=None, roi_poly=None):
        super().__init__(master)
        self.title(title)
        self.transient(master); self.grab_set()
        self.result = None

        scr_w = master.winfo_screenwidth()
        scr_h = master.winfo_screenheight()
        win_w = max(800, int(scr_w * 0.92))
        win_h = max(560, int(scr_h * 0.90))
        wx = (scr_w - win_w) // 2
        wy = max(0, (scr_h - win_h) // 2)
        self.geometry(f"{win_w}x{win_h}+{wx}+{wy}")
        self.minsize(800, 520)

        PANEL_W  = self._PANEL_W
        TOP_H    = 50
        BOT_H    = 70
        avail_w  = win_w - PANEL_W - 36
        avail_h  = win_h - TOP_H - BOT_H

        self.pil_full = Image.open(image_path).convert("RGB")
        self.ow, self.oh = self.pil_full.size
        self.scale = min(avail_w / self.ow, avail_h / self.oh, 1.0)

        sw, sh = int(self.ow * self.scale), int(self.oh * self.scale)
        self.preview = self.pil_full.resize((sw, sh), Image.LANCZOS)
        self.photo   = ImageTk.PhotoImage(self.preview)

        self.mask      = Image.new("L", (sw, sh), 0)
        self.mask_draw = ImageDraw.Draw(self.mask)

        self.history        = []
        self.MAX_HISTORY    = 20
        self.brush_radius   = 15
        self.last_xy        = None
        self.painting_button = None

        self._roi_poly_orig = roi_poly
        self._roi_mask      = None
        self._ppcm          = ppcm

        ttk.Label(self, text=instruction, padding=(8, 6),
                  font=("", 9), wraplength=win_w - PANEL_W - 20,
                  justify="left").pack(anchor="w", fill="x")

        body = ttk.Frame(self)
        body.pack(fill="both", expand=True, padx=(8, 0), pady=(0, 4))

        frame_canvas = ttk.Frame(body)
        frame_canvas.pack(side="left", fill="both", expand=True)

        hbar = ttk.Scrollbar(frame_canvas, orient="horizontal")
        vbar = ttk.Scrollbar(frame_canvas, orient="vertical")
        self.canvas = tk.Canvas(
            frame_canvas, cursor="dot", bg="#1a1a1a",
            highlightthickness=0,
            xscrollcommand=hbar.set, yscrollcommand=vbar.set)
        hbar.config(command=self.canvas.xview)
        vbar.config(command=self.canvas.yview)
        hbar.pack(side="bottom", fill="x")
        vbar.pack(side="right",  fill="y")
        self.canvas.pack(side="left", fill="both", expand=True)
        self.canvas.config(scrollregion=(0, 0, sw, sh))

        self._bg_item = self.canvas.create_image(0, 0, anchor="nw", image=self.photo)

        self._roi_overlay_pil   = Image.new("RGBA", (sw, sh), (0, 0, 0, 0))
        self._roi_overlay_photo = ImageTk.PhotoImage(self._roi_overlay_pil)
        self._roi_overlay_item  = self.canvas.create_image(0, 0, anchor="nw",
                                                            image=self._roi_overlay_photo)

        self.paint_pil   = Image.new("RGBA", (sw, sh), (0, 0, 0, 0))
        self.paint_photo = ImageTk.PhotoImage(self.paint_pil)
        self._paint_item = self.canvas.create_image(0, 0, anchor="nw", image=self.paint_photo)

        self._cursor_oval = self.canvas.create_oval(
            0, 0, 0, 0, outline="#ffff00", width=1, dash=(2, 2))

        self.canvas.bind("<Motion>",          self._on_motion)
        self.canvas.bind("<Leave>",           self._on_leave)
        self.canvas.bind("<ButtonPress-1>",   lambda e: self._start_paint(e, 1))
        self.canvas.bind("<B1-Motion>",       self._on_drag_paint)
        self.canvas.bind("<ButtonRelease-1>", self._end_paint)
        self.canvas.bind("<ButtonPress-3>",   lambda e: self._start_paint(e, 3))
        self.canvas.bind("<B3-Motion>",       self._on_drag_paint)
        self.canvas.bind("<ButtonRelease-3>", self._end_paint)
        self.canvas.bind("<MouseWheel>",      self._on_wheel)
        self.canvas.bind("<Button-4>",        lambda e: self._adjust_brush(+2))
        self.canvas.bind("<Button-5>",        lambda e: self._adjust_brush(-2))

        panel = ttk.Frame(body, width=PANEL_W)
        panel.pack(side="right", fill="y", padx=(6, 8), pady=0)
        panel.pack_propagate(False)

        frm_brush = ttk.LabelFrame(panel, text="Pincel", padding=(8, 6))
        frm_brush.pack(fill="x", pady=(4, 6))

        ttk.Label(frm_brush, text="Tamanho:").pack(anchor="w")
        self.var_brush = tk.IntVar(value=self.brush_radius)
        ttk.Scale(frm_brush, from_=2, to=80, orient="horizontal",
                  variable=self.var_brush,
                  command=self._on_brush_slider).pack(fill="x", pady=(2, 0))
        self.lbl_brush = ttk.Label(frm_brush, text=f"{self.brush_radius} px",
                                   foreground="#555")
        self.lbl_brush.pack(anchor="e")

        ttk.Label(frm_brush, text="Roda do mouse ou  [ ]  para ajustar.",
                  foreground="#888", font=("", 8), wraplength=PANEL_W - 24,
                  justify="left").pack(anchor="w", pady=(4, 0))

        frm_nav = ttk.LabelFrame(panel, text="Navegação", padding=(8, 6))
        frm_nav.pack(fill="x", pady=(0, 6))

        row1 = ttk.Frame(frm_nav); row1.pack(fill="x", pady=(0, 4))
        ttk.Button(row1, text="Zoom +  (+)",
                   command=lambda: self._set_zoom(1)).pack(side="left", expand=True, fill="x", padx=(0, 2))
        ttk.Button(row1, text="Zoom −  (−)",
                   command=lambda: self._set_zoom(-1)).pack(side="left", expand=True, fill="x")

        row2 = ttk.Frame(frm_nav); row2.pack(fill="x")
        ttk.Button(row2, text="Desfazer  (Z)",
                   command=self._undo).pack(side="left", expand=True, fill="x", padx=(0, 2))
        ttk.Button(row2, text="Limpar tudo",
                   command=self._clear).pack(side="left", expand=True, fill="x")

        self.var_show_roi     = tk.BooleanVar(value=True)
        self.var_roi_opacity  = tk.IntVar(value=40)
        self.var_limit_to_roi = tk.BooleanVar(value=False)

        frm_roi = ttk.LabelFrame(panel, text="ROI de calibração", padding=(8, 6))
        frm_roi.pack(fill="x", pady=(0, 6))

        if roi_poly:
            ttk.Checkbutton(frm_roi, text="Mostrar área do ROI",
                            variable=self.var_show_roi,
                            command=self._render_roi_overlay).pack(anchor="w")

            ttk.Label(frm_roi, text="Opacidade:").pack(anchor="w", pady=(6, 0))
            ttk.Scale(frm_roi, from_=0, to=100, orient="horizontal",
                      variable=self.var_roi_opacity,
                      command=lambda _: self._render_roi_overlay()).pack(fill="x")
            self.lbl_roi_opacity = ttk.Label(frm_roi, text="40%",
                                             foreground="#555")
            self.lbl_roi_opacity.pack(anchor="e")

            ttk.Separator(frm_roi, orient="horizontal").pack(fill="x", pady=6)

            ttk.Checkbutton(frm_roi, text="Limitar pincel ao ROI",
                            variable=self.var_limit_to_roi).pack(anchor="w")
        else:
            ttk.Label(frm_roi,
                      text="Nenhum ROI definido.\nMarque o ROI antes\nde abrir o pincel.",
                      foreground="#999", justify="left").pack(anchor="w")
            self.lbl_roi_opacity = None

        frm_act = ttk.Frame(panel)
        frm_act.pack(side="bottom", fill="x", pady=(0, 6))
        ttk.Button(frm_act, text="Cancelar",
                   command=self._cancel).pack(fill="x", pady=(0, 4))
        self.btn_ok = ttk.Button(frm_act, text="✔  Confirmar",
                                 command=self._confirm, state="disabled")
        self.btn_ok.pack(fill="x")

        self.lbl_stats = ttk.Label(
            self, padding=(8, 3), relief="sunken", anchor="w",
            text="Esquerdo = pintar  |  Direito = apagar  |  Roda / [ ] = pincel  |  Z = desfazer")
        self.lbl_stats.pack(fill="x", side="bottom")

        self.protocol("WM_DELETE_WINDOW", self._cancel)
        self.bind("<Escape>", lambda e: self._cancel())
        self.bind("<Return>", lambda e: self._confirm() if self._has_paint() else None)
        self.bind("<z>",      lambda e: self._undo())
        self.bind("<Z>",      lambda e: self._undo())
        self.bind("<bracketleft>",  lambda e: self._adjust_brush(-2))
        self.bind("<bracketright>", lambda e: self._adjust_brush(+2))
        self.bind("<plus>",        lambda e: self._set_zoom(1))
        self.bind("<equal>",       lambda e: self._set_zoom(1))
        self.bind("<KP_Add>",      lambda e: self._set_zoom(1))
        self.bind("<minus>",       lambda e: self._set_zoom(-1))
        self.bind("<KP_Subtract>", lambda e: self._set_zoom(-1))
        self.canvas.focus_set()

        self._update_stats()
        self._build_roi_mask()
        self._render_roi_overlay()

    def _build_roi_mask(self):
        self._roi_mask = None
        if not self._roi_poly_orig:
            return
        sw, sh = self.mask.size
        scaled = [(x * self.scale, y * self.scale) for x, y in self._roi_poly_orig]
        roi_img = Image.new("L", (sw, sh), 0)
        draw = ImageDraw.Draw(roi_img)
        flat = [coord for pt in scaled for coord in pt]
        draw.polygon(flat, fill=255)
        self._roi_mask = roi_img

    def _render_roi_overlay(self, _=None):
        sw, sh = self.mask.size
        if not self._roi_poly_orig or not self.var_show_roi.get():
            empty = Image.new("RGBA", (sw, sh), (0, 0, 0, 0))
            self._roi_overlay_photo = ImageTk.PhotoImage(empty)
            self.canvas.itemconfig(self._roi_overlay_item, image=self._roi_overlay_photo)
            return

        opacity = self.var_roi_opacity.get()
        if self.lbl_roi_opacity:
            self.lbl_roi_opacity.config(text=f"{opacity}%")

        alpha_fill    = int(opacity * 2.55)
        alpha_outline = min(255, alpha_fill + 120)

        scaled = [(x * self.scale, y * self.scale) for x, y in self._roi_poly_orig]
        overlay = Image.new("RGBA", (sw, sh), (0, 0, 0, 0))
        draw = ImageDraw.Draw(overlay)
        flat = [coord for pt in scaled for coord in pt]
        draw.polygon(flat, fill=(50, 140, 255, alpha_fill))
        
        n = len(scaled)
        for i in range(n):
            x1, y1 = scaled[i]
            x2, y2 = scaled[(i + 1) % n]
            draw.line((x1, y1, x2, y2), fill=(50, 140, 255, alpha_outline), width=2)

        self._roi_overlay_photo = ImageTk.PhotoImage(overlay)
        self.canvas.itemconfig(self._roi_overlay_item, image=self._roi_overlay_photo)

    def _apply_roi_constraint_if_enabled(self):
        if not self.var_limit_to_roi.get() or self._roi_mask is None:
            return
        if self._roi_mask.size != self.mask.size:
            self._build_roi_mask()
        if self._roi_mask is None:
            return
        self.mask = ImageChops.multiply(self.mask, self._roi_mask)
        self.mask_draw = ImageDraw.Draw(self.mask)

    def _set_zoom(self, delta):
        new_scale = self.scale * (1.2 if delta > 0 else 0.8)
        if new_scale < 0.05 or new_scale > 10.0: return

        sw, sh = int(self.ow * new_scale), int(self.oh * new_scale)
        self.preview = self.pil_full.resize((sw, sh), Image.LANCZOS)
        self.photo = ImageTk.PhotoImage(self.preview)
        self.canvas.itemconfig(self._bg_item, image=self.photo)

        self.mask = self.mask.resize((sw, sh), Image.NEAREST)
        self.mask_draw = ImageDraw.Draw(self.mask)

        self.scale = new_scale
        self.canvas.config(scrollregion=(0, 0, sw, sh))

        self._roi_overlay_pil = Image.new("RGBA", (sw, sh), (0, 0, 0, 0))
        self._roi_overlay_photo = ImageTk.PhotoImage(self._roi_overlay_pil)
        self.canvas.itemconfig(self._roi_overlay_item, image=self._roi_overlay_photo)

        self._build_roi_mask()
        self._render_roi_overlay()
        self._render_mask()
        self._update_stats()

    def _on_brush_slider(self, _):
        self.brush_radius = int(float(self.var_brush.get()))
        self.lbl_brush.config(text=f"{self.brush_radius} px")
        if self.last_xy: self._update_cursor(*self.last_xy)

    def _adjust_brush(self, delta):
        new = max(2, min(80, self.brush_radius + delta))
        self.brush_radius = new
        self.var_brush.set(new); self.lbl_brush.config(text=f"{new} px")
        if self.last_xy: self._update_cursor(*self.last_xy)

    def _on_wheel(self, e):
        delta = 2 if e.delta > 0 else -2
        self._adjust_brush(delta)

    def _update_cursor(self, x, y):
        r = self.brush_radius
        self.canvas.coords(self._cursor_oval, x - r, y - r, x + r, y + r)

    def _on_motion(self, e):
        cx, cy = self.canvas.canvasx(e.x), self.canvas.canvasy(e.y)
        self.last_xy = (cx, cy)
        self._update_cursor(cx, cy)

    def _on_leave(self, _):
        self.canvas.coords(self._cursor_oval, -10, -10, -10, -10)

    def _push_history(self):
        self.history.append(self.mask.copy())
        if len(self.history) > self.MAX_HISTORY:
            self.history.pop(0)

    def _start_paint(self, e, button):
        self._push_history()
        self.painting_button = button
        cx, cy = self.canvas.canvasx(e.x), self.canvas.canvasy(e.y)
        self._paint_at(cx, cy, button == 1)

    def _on_drag_paint(self, e):
        if self.painting_button is None: return
        cx, cy = self.canvas.canvasx(e.x), self.canvas.canvasy(e.y)
        if self.last_xy:
            lx, ly = self.last_xy
            self._paint_line(lx, ly, cx, cy, self.painting_button == 1)
        self.last_xy = (cx, cy)
        self._update_cursor(cx, cy)

    def _end_paint(self, e):
        self.painting_button = None
        self._render_mask()
        self._update_stats()

    def _paint_at(self, x, y, paint=True):
        r = self.brush_radius
        fill = 255 if paint else 0
        self.mask_draw.ellipse((x - r, y - r, x + r, y + r), fill=fill)
        if paint:
            self._apply_roi_constraint_if_enabled()
        self._render_mask()

    def _paint_line(self, x0, y0, x1, y1, paint=True):
        r = self.brush_radius
        fill = 255 if paint else 0
        self.mask_draw.line((x0, y0, x1, y1), fill=fill, width=r * 2)
        self.mask_draw.ellipse((x1 - r, y1 - r, x1 + r, y1 + r), fill=fill)
        if paint:
            self._apply_roi_constraint_if_enabled()
        self._render_mask()

    def _render_mask(self):
        red = Image.new("RGBA", self.mask.size, (255, 30, 30, 0))
        alpha = self.mask.point(lambda v: 110 if v > 127 else 0)
        red.putalpha(alpha)
        self.paint_pil = red
        self.paint_photo = ImageTk.PhotoImage(self.paint_pil)
        self.canvas.itemconfig(self._paint_item, image=self.paint_photo)

    def _has_paint(self):
        return self.mask.getbbox() is not None

    def _painted_pixels_preview(self):
        return sum(1 for v in self.mask.getdata() if v > 127)

    def _painted_pixels_orig(self):
        if not self._has_paint(): return 0
        full = self.mask.resize((self.ow, self.oh), Image.NEAREST)
        return sum(1 for v in full.getdata() if v > 127)

    def _update_stats(self):
        if not self._has_paint():
            self.lbl_stats.config(
                text="Pinte com o botão ESQUERDO. Apague com o DIREITO. "
                     "Roda do mouse ou [ ] = pincel. Z = desfazer.")
            self.btn_ok.config(state="disabled")
            return
        prev_px = self._painted_pixels_preview()
        est_orig = int(prev_px / (self.scale ** 2))
        text = f"Pintado (preview): {prev_px:,} px  |  estimado na imagem original: ~{est_orig:,} px"
        if self._ppcm:
            est_cm2 = est_orig / (self._ppcm ** 2)
            text += f"  |  ≈ {est_cm2:.3f} cm²"
        self.lbl_stats.config(text=text)
        self.btn_ok.config(state="normal")

    def _undo(self):
        if not self.history: return
        old_mask = self.history.pop()
        if old_mask.size != self.mask.size:
            old_mask = old_mask.resize(self.mask.size, Image.NEAREST)
        self.mask = old_mask
        self.mask_draw = ImageDraw.Draw(self.mask)
        self._render_mask(); self._update_stats()

    def _clear(self):
        if self._has_paint(): self._push_history()
        sw, sh = self.preview.size
        self.mask = Image.new("L", (sw, sh), 0)
        self.mask_draw = ImageDraw.Draw(self.mask)
        self._render_mask(); self._update_stats()

    def _confirm(self):
        if not self._has_paint(): return
        self.config(cursor="wait"); self.update_idletasks()
        try:
            n = self._painted_pixels_orig()
        finally:
            self.config(cursor="")
        self.result = {"painted_pixels_orig": n}
        self.destroy()

    def _cancel(self):
        self.result = None; self.destroy()

def ask_brush(master, image_path, title, instruction, ppcm=None, roi_poly=None):
    bp = BrushPainter(master, image_path, title, instruction, ppcm=ppcm, roi_poly=roi_poly)
    master.wait_window(bp)
    return bp.result

class RoiSelector(tk.Toplevel):
    def __init__(self, master, image_path, title, instruction):
        super().__init__(master)
        self.title(title); self.transient(master); self.grab_set()
        self.result = None

        self.pil_full = Image.open(image_path).convert("RGB")
        self.ow, self.oh = self.pil_full.size
        
        screen_w = master.winfo_screenwidth() * 0.85
        screen_h = master.winfo_screenheight() * 0.75
        self.scale = min(screen_w / self.ow, screen_h / self.oh, 1.0)
        
        sw, sh = int(self.ow * self.scale), int(self.oh * self.scale)
        self.preview = self.pil_full.resize((sw, sh), Image.LANCZOS)
        self.photo = ImageTk.PhotoImage(self.preview)

        self.vertices = []
        self.selected_vertex = None
        self.mode = None
        self.drag_anchor = None
        self.drag_start_polys = None
        self.angle_total = 0.0

        self._fill = None; self._edges = []; self._vertex_items = []
        self._rot_line = None; self._rot_handle = None

        ttk.Label(self, text=instruction, padding=(10, 8), font=("", 10),
                  wraplength=800, justify="left").pack(anchor="w")

        frame_canvas = ttk.Frame(self)
        frame_canvas.pack(fill="both", expand=True, padx=10, pady=4)

        hbar = ttk.Scrollbar(frame_canvas, orient="horizontal")
        vbar = ttk.Scrollbar(frame_canvas, orient="vertical")

        self.canvas = tk.Canvas(frame_canvas, cursor="crosshair", bg="#222",
                                highlightthickness=0, xscrollcommand=hbar.set, yscrollcommand=vbar.set)
        
        hbar.config(command=self.canvas.xview)
        vbar.config(command=self.canvas.yview)

        hbar.pack(side="bottom", fill="x")
        vbar.pack(side="right", fill="y")
        self.canvas.pack(side="left", fill="both", expand=True)

        cw = min(sw, int(screen_w))
        ch = min(sh, int(screen_h))
        self.canvas.config(width=cw, height=ch, scrollregion=(0, 0, sw, sh))

        self._bg_item = self.canvas.create_image(0, 0, anchor="nw", image=self.photo)
        
        self.canvas.bind("<ButtonPress-1>",   self._on_press)
        self.canvas.bind("<B1-Motion>",       self._on_drag)
        self.canvas.bind("<ButtonRelease-1>", self._on_release)
        self.canvas.bind("<Motion>",          self._on_motion)
        self.canvas.bind("<Button-3>",        self._on_right_click)

        ctrl = ttk.Frame(self, padding=(10, 4)); ctrl.pack(fill="x")
        ttk.Label(ctrl, text="Rotação:").pack(side="left")
        self.var_angle = tk.DoubleVar(value=0.0)
        self.scale_angle = ttk.Scale(ctrl, from_=-180, to=180, orient="horizontal",
                                     length=240, variable=self.var_angle,
                                     command=self._on_angle_slider)
        self.scale_angle.pack(side="left", padx=8)
        self.lbl_angle = ttk.Label(ctrl, text="0.0°", width=8); self.lbl_angle.pack(side="left")
        for txt, dv in (("−5°", -5), ("−1°", -1), ("+1°", +1), ("+5°", +5)):
            ttk.Button(ctrl, text=txt, width=4,
                       command=lambda d=dv: self._step_angle(d)).pack(side="left", padx=2)
        ttk.Button(ctrl, text="Resetar 0°", width=10,
                   command=self._reset_angle).pack(side="left", padx=8)

        ttk.Button(ctrl, text="Zoom +", command=lambda: self._set_zoom(1)).pack(side="left", padx=4)
        ttk.Button(ctrl, text="Zoom -", command=lambda: self._set_zoom(-1)).pack(side="left", padx=4)

        ctrl2 = ttk.Frame(self, padding=(10, 0)); ctrl2.pack(fill="x")
        ttk.Label(ctrl2, text="Vértices:").pack(side="left")
        ttk.Button(ctrl2, text="Dobrar (adiciona meios)",
                   command=self._add_midpoints).pack(side="left", padx=4)
        ttk.Button(ctrl2, text="Remover selecionado (Del)",
                   command=self._remove_selected_vertex).pack(side="left", padx=4)
        self.lbl_vcount = ttk.Label(ctrl2, text="(0)", foreground="#666")
        self.lbl_vcount.pack(side="left", padx=8)

        self.lbl_dim = ttk.Label(self, padding=(10, 4), wraplength=800,
            justify="left",
            text=("Arraste o mouse para criar o polígono inicial. "
                  "Arraste cada vértice; clique numa aresta = adicionar vértice; "
                  "botão direito num vértice = remover; arraste pelo interior = mover; "
                  "botão direito no interior = apagar tudo."))
        self.lbl_dim.pack(anchor="w")

        bar = ttk.Frame(self, padding=(10, 8)); bar.pack(fill="x")
        ttk.Button(bar, text="Limpar", command=self._clear).pack(side="left")
        ttk.Button(bar, text="Cancelar", command=self._cancel).pack(side="right")
        self.btn_ok = ttk.Button(bar, text="Confirmar",
                                 command=self._confirm, state="disabled")
        self.btn_ok.pack(side="right", padx=6)

        self.protocol("WM_DELETE_WINDOW", self._cancel)
        self.bind("<Escape>", lambda e: self._cancel())
        self.bind("<Return>", lambda e: self._confirm() if self._has_poly() else None)
        self.bind("<Delete>", lambda e: self._remove_selected_vertex())
        self.bind("<Left>",        lambda e: self._step_angle(-1))
        self.bind("<Right>",       lambda e: self._step_angle(+1))
        self.bind("<Shift-Left>",  lambda e: self._step_angle(-5))
        self.bind("<Shift-Right>", lambda e: self._step_angle(+5))
        
        self.bind("<plus>", lambda e: self._set_zoom(1))
        self.bind("<equal>", lambda e: self._set_zoom(1))
        self.bind("<KP_Add>", lambda e: self._set_zoom(1))
        self.bind("<minus>", lambda e: self._set_zoom(-1))
        self.bind("<KP_Subtract>", lambda e: self._set_zoom(-1))
        
        self.canvas.focus_set()

        self.update_idletasks()
        x = master.winfo_rootx() + (master.winfo_width() - self.winfo_width()) // 2
        y = master.winfo_rooty() + (master.winfo_height() - self.winfo_height()) // 2
        self.geometry(f"+{max(0, x)}+{max(0, y)}")

    def _set_zoom(self, delta):
        new_scale = self.scale * (1.2 if delta > 0 else 0.8)
        if new_scale < 0.05 or new_scale > 10.0: return

        sw, sh = int(self.ow * new_scale), int(self.oh * new_scale)
        self.preview = self.pil_full.resize((sw, sh), Image.LANCZOS)
        self.photo = ImageTk.PhotoImage(self.preview)
        self.canvas.itemconfig(self._bg_item, image=self.photo)

        factor = new_scale / self.scale
        self.vertices = [(x * factor, y * factor) for (x, y) in self.vertices]
        if self.drag_anchor:
            self.drag_anchor = (self.drag_anchor[0] * factor, self.drag_anchor[1] * factor)
        if self.drag_start_polys:
            self.drag_start_polys = [(x * factor, y * factor) for (x, y) in self.drag_start_polys]

        self.scale = new_scale
        self.canvas.config(scrollregion=(0, 0, sw, sh))
        self._redraw()

    def _has_poly(self): return len(self.vertices) >= 3
    def _centroid(self): return polygon_centroid(self.vertices)
    def _bbox(self):
        xs = [v[0] for v in self.vertices]; ys = [v[1] for v in self.vertices]
        return (min(xs), min(ys), max(xs), max(ys))

    def _redraw(self):
        for it in self._edges + list(self._vertex_items): self.canvas.delete(it)
        if self._fill: self.canvas.delete(self._fill)
        if self._rot_line: self.canvas.delete(self._rot_line)
        if self._rot_handle: self.canvas.delete(self._rot_handle)
        self._edges = []; self._vertex_items = []
        self._fill = self._rot_line = self._rot_handle = None

        n = len(self.vertices)
        self.lbl_vcount.config(text=f"({n})")
        if n < 2: self._update_dim_label(); return

        if n >= 3:
            flat = [c for p in self.vertices for c in p]
            self._fill = self.canvas.create_polygon(
                *flat, fill=RECT_COLOR, outline="", stipple="gray12")
        for i in range(n):
            x1, y1 = self.vertices[i]; x2, y2 = self.vertices[(i + 1) % n]
            self._edges.append(self.canvas.create_line(
                x1, y1, x2, y2, fill=RECT_COLOR, width=2))
        for i, (x, y) in enumerate(self.vertices):
            r = VERTEX_RADIUS
            fill = VERTEX_SEL if i == self.selected_vertex else VERTEX_FILL
            self._vertex_items.append(self.canvas.create_oval(
                x - r, y - r, x + r, y + r, fill=fill, outline=VERTEX_OUTL, width=2))
        if n >= 3:
            cx, cy = self._centroid()
            x1, y1, _, _ = self._bbox()
            rx, ry = cx, y1 - ROT_OFFSET
            self._rot_line = self.canvas.create_line(cx, y1, rx, ry, fill=ROT_COLOR, width=1)
            r = VERTEX_RADIUS + 1
            self._rot_handle = self.canvas.create_oval(
                rx - r, ry - r, rx + r, ry + r, fill=ROT_COLOR, outline="white", width=1)
        self._update_dim_label()

    def _update_dim_label(self):
        n = len(self.vertices)
        if n < 3: self.btn_ok.config(state="disabled"); return
        s = self.scale
        verts_orig = [(x / s, y / s) for (x, y) in self.vertices]
        area_px = polygon_area(verts_orig)
        x1, y1, x2, y2 = self._bbox()
        bw, bh = (x2 - x1) / s, (y2 - y1) / s
        self.lbl_dim.config(
            text=(f"Polígono: {n} vértices  |  bbox = {bw:.0f} × {bh:.0f} px  |  "
                  f"área = {int(area_px):,} px²  |  rotação = {self.angle_total:.1f}°"))
        self.btn_ok.config(state="normal")

    def _hit_vertex(self, x, y):
        for i, (vx, vy) in enumerate(self.vertices):
            if (vx - x) ** 2 + (vy - y) ** 2 <= VERTEX_HIT ** 2: return i
        return None

    def _hit_rotation(self, x, y):
        if not self._has_poly(): return False
        cx, _ = self._centroid()
        x1, y1, _, _ = self._bbox()
        rx, ry = cx, y1 - ROT_OFFSET
        return (rx - x) ** 2 + (ry - y) ** 2 <= VERTEX_HIT ** 2

    def _hit_edge(self, x, y):
        n = len(self.vertices)
        if n < 2: return None
        best = None; best_d2 = EDGE_HIT ** 2
        for i in range(n):
            ax, ay = self.vertices[i]; bx, by = self.vertices[(i + 1) % n]
            qx, qy, t, d2 = closest_point_on_segment(x, y, ax, ay, bx, by)
            if 0.05 < t < 0.95 and d2 < best_d2:
                best = (i, (qx, qy)); best_d2 = d2
        return best

    def _point_inside(self, x, y):
        return self._has_poly() and point_in_polygon(x, y, self.vertices)

    def _on_motion(self, e):
        if self.mode is not None: return
        cx, cy = self.canvas.canvasx(e.x), self.canvas.canvasy(e.y)
        if self._hit_rotation(cx, cy): self.canvas.config(cursor="exchange"); return
        if self._hit_vertex(cx, cy) is not None: self.canvas.config(cursor="hand2"); return
        if self._hit_edge(cx, cy) is not None: self.canvas.config(cursor="plus"); return
        if self._point_inside(cx, cy): self.canvas.config(cursor="fleur"); return
        self.canvas.config(cursor="crosshair")

    def _on_press(self, e):
        cx, cy = self.canvas.canvasx(e.x), self.canvas.canvasy(e.y)
        if self._hit_rotation(cx, cy):
            self.mode = "rotate"
            ccx, ccy = self._centroid()
            self._rot_start_angle = math.degrees(math.atan2(cy - ccy, cx - ccx))
            self._rot_start_total = self.angle_total
            self._rot_start_verts = list(self.vertices)
            self._rot_center = (ccx, ccy); return
            
        vi = self._hit_vertex(cx, cy)
        if vi is not None:
            self.mode = "vertex"; self.selected_vertex = vi
            self.drag_anchor = (cx - self.vertices[vi][0], cy - self.vertices[vi][1])
            self._redraw(); return
            
        edge = self._hit_edge(cx, cy)
        if edge is not None:
            i, (qx, qy) = edge
            self.vertices.insert(i + 1, (qx, qy))
            self.selected_vertex = i + 1
            self.mode = "vertex"
            self.drag_anchor = (cx - qx, cy - qy)
            self._redraw()
            return

        if self._point_inside(cx, cy):
            self.mode = "move"
            self.drag_anchor = (cx, cy)
            self.drag_start_polys = list(self.vertices); return
            
        if self._has_poly():
            return
            
        self.mode = "draw"; self.drag_anchor = (cx, cy)
        self.angle_total = 0.0; self.var_angle.set(0); self.lbl_angle.config(text="0.0°")
        self.selected_vertex = None
        self.vertices = [(cx, cy)] * 4
        self._redraw()

    def _on_drag(self, e):
        cx, cy = self.canvas.canvasx(e.x), self.canvas.canvasy(e.y)
        if self.mode == "draw":
            x0, y0 = self.drag_anchor; x1, y1 = cx, cy
            self.vertices = [(x0, y0), (x1, y0), (x1, y1), (x0, y1)]
            self._redraw(); return
        if self.mode == "vertex":
            dx, dy = self.drag_anchor; i = self.selected_vertex
            self.vertices[i] = (cx - dx, cy - dy); self._redraw(); return
        if self.mode == "move":
            x0, y0 = self.drag_anchor; ddx, ddy = cx - x0, cy - y0
            self.vertices = [(v[0] + ddx, v[1] + ddy) for v in self.drag_start_polys]
            self._redraw(); return
        if self.mode == "rotate":
            ccx, ccy = self._rot_center
            cur_angle = math.degrees(math.atan2(cy - ccy, cx - ccx))
            delta = cur_angle - self._rot_start_angle
            a = math.radians(delta); ca, sa = math.cos(a), math.sin(a)
            self.vertices = [
                (ccx + (vx - ccx) * ca - (vy - ccy) * sa,
                 ccy + (vx - ccx) * sa + (vy - ccy) * ca)
                for (vx, vy) in self._rot_start_verts]
            self.angle_total = ((self._rot_start_total + delta + 180) % 360) - 180
            self.var_angle.set(self.angle_total)
            self.lbl_angle.config(text=f"{self.angle_total:.1f}°")
            self._redraw(); return

    def _on_release(self, e):
        if self.mode == "draw":
            x1, y1, x2, y2 = self._bbox()
            if (x2 - x1) < 5 or (y2 - y1) < 5: self._clear()
        self.mode = None; self.drag_anchor = None; self._on_motion(e)

    def _on_right_click(self, e):
        cx, cy = self.canvas.canvasx(e.x), self.canvas.canvasy(e.y)
        vi = self._hit_vertex(cx, cy)
        if vi is not None:
            if len(self.vertices) <= 3:
                messagebox.showinfo("Mínimo", "Pelo menos 3 vértices."); return
            del self.vertices[vi]
            if self.selected_vertex == vi: self.selected_vertex = None
            elif self.selected_vertex is not None and self.selected_vertex > vi:
                self.selected_vertex -= 1
            self._redraw()
            return
            
        if self._point_inside(cx, cy):
            if messagebox.askyesno("Apagar", "Deseja apagar a seleção atual?"):
                self._clear()
            return

    def _remove_selected_vertex(self):
        if self.selected_vertex is None: return
        if len(self.vertices) <= 3:
            messagebox.showinfo("Mínimo", "Pelo menos 3 vértices."); return
        del self.vertices[self.selected_vertex]
        self.selected_vertex = None; self._redraw()

    def _add_midpoints(self):
        if not self._has_poly(): return
        n = len(self.vertices); new = []
        for i in range(n):
            ax, ay = self.vertices[i]; bx, by = self.vertices[(i + 1) % n]
            new.append((ax, ay))
            new.append(((ax + bx) / 2, (ay + by) / 2))
        self.vertices = new; self.selected_vertex = None; self._redraw()

    def _on_angle_slider(self, _):
        if not self._has_poly(): self.var_angle.set(0); return
        target = float(self.var_angle.get())
        delta = target - self.angle_total
        if abs(delta) < 0.01: return
        self._apply_rotation(delta); self.angle_total = target
        self.lbl_angle.config(text=f"{target:.1f}°"); self._redraw()

    def _step_angle(self, delta):
        if not self._has_poly(): return
        self._apply_rotation(delta)
        v = ((self.angle_total + delta + 180) % 360) - 180
        self.angle_total = v; self.var_angle.set(v); self.lbl_angle.config(text=f"{v:.1f}°")
        self._redraw()

    def _reset_angle(self):
        self.angle_total = 0.0; self.var_angle.set(0); self.lbl_angle.config(text="0.0°")

    def _apply_rotation(self, deg):
        cx, cy = self._centroid()
        a = math.radians(deg); ca, sa = math.cos(a), math.sin(a)
        self.vertices = [
            (cx + (vx - cx) * ca - (vy - cy) * sa,
             cy + (vx - cx) * sa + (vy - cy) * ca) for (vx, vy) in self.vertices]

    def _clear(self):
        self.vertices = []; self.selected_vertex = None; self.angle_total = 0.0
        self.var_angle.set(0); self.lbl_angle.config(text="0.0°"); self._redraw()

    def _confirm(self):
        if not self._has_poly(): return
        s = self.scale
        pts_orig = [(x / s, y / s) for (x, y) in self.vertices]
        self.result = {"poly_orig": pts_orig,
                       "angle_deg": self.angle_total,
                       "area_orig_px": polygon_area(pts_orig),
                       "n_vertices": len(pts_orig)}
        self.destroy()

    def _cancel(self): self.result = None; self.destroy()

def ask_roi(master, image_path, title, instruction):
    sel = RoiSelector(master, image_path, title, instruction)
    master.wait_window(sel)
    return sel.result

class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("Análise de Herbivoria — Área Foliar Consumida")
        self.geometry("960x620"); self.minsize(880, 580)

        self.results = []
        self.photo_metadata = {}
        self.imported_samples = []
        self._reset_current()
        self._build_ui()

    def _reset_current(self):
        self.cur_path = None
        self.cur_calib = None
        self.cur_brush_px = None
        self.cur_remaining = None
        self.cur_consumed = None
        self.cur_ppcm = None
        self.cur_photo_date = ""

    def _build_ui(self):
        menubar = tk.Menu(self)
        
        filemenu = tk.Menu(menubar, tearoff=0)
        filemenu.add_command(label="Salvar Projeto (JSON)...", command=self._save_project)
        filemenu.add_command(label="Carregar Projeto (JSON)...", command=self._load_project)
        filemenu.add_separator()
        filemenu.add_command(label="Importar Lista (CSV)...", command=self._import_csv)
        filemenu.add_separator()
        filemenu.add_command(label="Sair", command=self.quit)
        menubar.add_cascade(label="Arquivo", menu=filemenu)
        
        helpmenu = tk.Menu(menubar, tearoff=0)
        helpmenu.add_command(label="Ver Manual de Uso (PDF)", command=self._open_manual)
        menubar.add_cascade(label="Ajuda", menu=helpmenu)
        
        self.config(menu=menubar)

        nb = ttk.Notebook(self); nb.pack(fill="both", expand=True, padx=8, pady=8)
        self.tab_anal = ttk.Frame(nb); nb.add(self.tab_anal, text="Análise")
        self.tab_res  = ttk.Frame(nb); nb.add(self.tab_res,  text="Resultados")
        self.tab_meta = ttk.Frame(nb); nb.add(self.tab_meta, text="Observações")
        
        self._build_analysis_tab()
        self._build_results_tab()
        self._build_meta_tab()
        
        self.status = tk.StringVar(value="Pronto.  |  Atalhos: F1 = ROI  |  F2 = Pincel  |  F3 = Calcular  |  F4 = Devorada")
        ttk.Label(self, textvariable=self.status, relief="sunken",
                  anchor="w").pack(fill="x", side="bottom")

        self.bind("<F1>", lambda e: self._mark_calib())
        self.bind("<F2>", lambda e: self._mark_meas_brush())
        self.bind("<F3>", lambda e: self._calculate())
        self.bind("<F4>", lambda e: self._completely_devoured())

    def _open_manual(self):
        if getattr(sys, 'frozen', False):
            base_dir = Path(sys.executable).parent
        else:
            base_dir = Path(__file__).parent
            
        manual_path = base_dir / "Manual_de_Uso_Leaf_Analyzer.pdf"
        
        if not manual_path.exists():
            messagebox.showerror("Erro", f"Manual não encontrado:\n{manual_path}\n\nColoque o arquivo PDF na mesma pasta do programa.")
            return
            
        try:
            if os.name == 'nt':
                os.startfile(manual_path)
            elif sys.platform == 'darwin':
                subprocess.call(['open', manual_path])
            else:
                subprocess.call(['xdg-open', manual_path])
        except Exception as e:
            messagebox.showerror("Erro", f"Não foi possível abrir o manual.\n{e}")

    def _build_analysis_tab(self):
        f = self.tab_anal
        info = ttk.LabelFrame(f, text="Como funciona")
        info.pack(fill="x", padx=10, pady=(10, 6))
        ttk.Label(info, wraplength=900, justify="left", padding=(8, 6),
            text=(
                "Para cada amostra:\n"
                "  1) Selecione a foto da folha consumida.\n"
                "  2) Marque o ROI de CALIBRAÇÃO [F1] desenhando um polígono cobrindo a região "
                "que originalmente tinha a área conhecida (ex: 6 cm²).\n"
                "  3) Use o PINCEL MANUAL [F2] para pintar com o mouse as áreas consumidas. "
                "Esquerdo = pintar, Direito = apagar, Roda do mouse / [ ] = tamanho.\n"
                "  4) CALCULAR [F3] para obter o consumido em cm². Adicione à tabela e exporte CSV.\n"
                "  Atalhos rápidos: F1 = ROI de calibração  |  F2 = Pincel  |  F3 = Calcular  |  F4 = Folha devorada"
            )).pack(anchor="w")

        top = ttk.LabelFrame(f, text="Identificação da amostra")
        top.pack(fill="x", padx=10, pady=6)
        ttk.Label(top, text="ID amostra:").grid(row=0, column=0, padx=6, pady=4, sticky="w")
        self.var_sample = tk.StringVar()
        self.cb_sample = ttk.Combobox(top, textvariable=self.var_sample, width=20)
        self.cb_sample.grid(row=0, column=1, padx=6, sticky="w")
        
        ttk.Label(top, text="Tratamento:").grid(row=0, column=2, padx=6, sticky="w")
        self.var_treat = tk.StringVar()
        self.cb_treat = ttk.Combobox(top, textvariable=self.var_treat, width=20)
        self.cb_treat.grid(row=0, column=3, padx=6, sticky="w")
        
        ttk.Label(top, text="Área conhecida (cm²):")\
            .grid(row=1, column=0, padx=6, pady=4, sticky="w")
        self.var_known_area = tk.StringVar(value="6.0")
        ttk.Entry(top, textvariable=self.var_known_area, width=10).grid(row=1, column=1, padx=6, sticky="w")

        leaf = ttk.LabelFrame(f, text="Folha")
        leaf.pack(fill="x", padx=10, pady=6)
        ttk.Label(leaf, text="Imagem:").grid(row=0, column=0, padx=6, pady=6, sticky="w")
        self.var_img = tk.StringVar()
        ttk.Entry(leaf, textvariable=self.var_img, width=70)\
            .grid(row=0, column=1, padx=6, sticky="we")
        ttk.Button(leaf, text="Procurar...", command=self._pick_image)\
            .grid(row=0, column=2, padx=4)

        ttk.Button(leaf, text="1) ROI de calibração  [F1]",
                   command=self._mark_calib).grid(row=1, column=0, columnspan=2, padx=6, pady=4, sticky="w")
        self.lbl_calib = ttk.Label(leaf, text="—", foreground="#555")
        self.lbl_calib.grid(row=1, column=2, columnspan=2, sticky="w", padx=6)

        ttk.Button(leaf, text="2) Pincel manual (consumo)  [F2]",
                   command=self._mark_meas_brush).grid(row=2, column=0, columnspan=2, padx=6, pady=4, sticky="w")
        self.lbl_meas = ttk.Label(leaf, text="—", foreground="#555")
        self.lbl_meas.grid(row=2, column=2, columnspan=2, sticky="w", padx=6)

        ttk.Button(leaf, text="3) Calcular  [F3]",
                   command=self._calculate).grid(row=3, column=0, columnspan=2, padx=6, pady=6, sticky="w")
        self.lbl_result = ttk.Label(leaf, text="—", foreground="#005c1f", font=("", 10, "bold"))
        self.lbl_result.grid(row=3, column=2, columnspan=2, sticky="w", padx=6)
        
        ttk.Button(leaf, text="Folha completamente devorada  [F4]",
                   command=self._completely_devoured).grid(row=4, column=0, columnspan=2, padx=6, pady=4, sticky="w")
                   
        leaf.columnconfigure(1, weight=1)

        bottom = ttk.Frame(f); bottom.pack(fill="x", padx=10, pady=10)
        
        self.var_auto_add = tk.BooleanVar(value=False)
        ttk.Checkbutton(bottom, text="Adicionar direto na tabela ao calcular", variable=self.var_auto_add).pack(side="left")
        
        ttk.Button(bottom, text="Adicionar resultado à tabela",
                   command=self._add_result).pack(side="right")
        ttk.Button(bottom, text="Limpar campos",
                   command=self._clear_fields).pack(side="right", padx=6)

    def _build_results_tab(self):
        f = self.tab_res
        cols = ("sample", "treatment", "known_cm2",
                "remaining_cm2", "consumed_cm2", "ppcm", "timestamp", "photo_date")
        headings = {
            "sample": "Amostra", "treatment": "Tratamento",
            "known_cm2": "Folha inicial (cm²)",
            "remaining_cm2": "Restante (cm²)",
            "consumed_cm2": "Consumido (cm²)",
            "ppcm": "px/cm",
            "timestamp": "Data/hora",
            "photo_date": "Data da foto",
        }
        self.tree = ttk.Treeview(f, columns=cols, show="headings", height=18)
        for c, t in headings.items():
            self.tree.heading(c, text=t); self.tree.column(c, width=120, anchor="center")
        self.tree.pack(fill="both", expand=True, padx=10, pady=8)
        bar = ttk.Frame(f); bar.pack(fill="x", padx=10, pady=6)
        ttk.Button(bar, text="Remover selecionado",
                   command=self._remove_selected).pack(side="left")
        ttk.Button(bar, text="Limpar Todos os Dados",
                   command=self._reset_all_data).pack(side="left", padx=6)
        ttk.Button(bar, text="Exportar CSV (para Prism)",
                   command=self._export_csv).pack(side="right")

    def _build_meta_tab(self):
        f = self.tab_meta
        cols = ("sample", "photo_date")
        self.meta_tree = ttk.Treeview(f, columns=cols, show="headings", height=18)
        self.meta_tree.heading("sample", text="ID da Amostra")
        self.meta_tree.heading("photo_date", text="Data/Hora da Foto")
        self.meta_tree.column("sample", width=200, anchor="center")
        self.meta_tree.column("photo_date", width=200, anchor="center")
        self.meta_tree.pack(fill="both", expand=True, padx=10, pady=8)
        
        bar = ttk.Frame(f)
        bar.pack(fill="x", padx=10, pady=6)
        ttk.Label(bar, text="Editar Data/Hora:").pack(side="left", padx=4)
        self.var_edit_date = tk.StringVar()
        ttk.Entry(bar, textvariable=self.var_edit_date, width=25).pack(side="left", padx=4)
        ttk.Button(bar, text="Atualizar Selecionado", command=self._update_selected_meta).pack(side="left", padx=4)
        
        self.meta_tree.bind("<<TreeviewSelect>>", self._on_meta_select)

    def _save_project(self):
        path = filedialog.asksaveasfilename(defaultextension=".json", filetypes=[("JSON", "*.json")])
        if not path: return
        data = {
            "results": self.results,
            "metadata": self.photo_metadata
        }
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=4)
        self._set_status(f"Projeto salvo: {path}")

    def _load_project(self):
        path = filedialog.askopenfilename(filetypes=[("JSON", "*.json")])
        if not path: return
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        self.results = data.get("results", [])
        self.photo_metadata = data.get("metadata", {})
        self._refresh_trees()
        self._set_status(f"Projeto carregado: {path}")

    def _import_csv(self):
        path = filedialog.askopenfilename(filetypes=[("CSV", "*.csv")])
        if not path: return
        
        encodings = ["utf-8-sig", "latin-1", "utf-8", "cp1252"]
        success = False
        
        for enc in encodings:
            try:
                with open(path, "r", encoding=enc, newline='') as f:
                    content = f.read()
                success = True
                break
            except UnicodeDecodeError:
                continue
                
        if not success:
            messagebox.showerror("Erro", "Não foi possível detectar a codificação do arquivo.")
            return

        lines = [line.strip() for line in content.splitlines() if line.strip()]
        if not lines: return
            
        if lines[0].lower().startswith("sep="):
            lines.pop(0)
            
        if not lines: return

        delimiter = ';' if ';' in lines[0] else ','
        reader = csv.reader(lines, delimiter=delimiter)
        
        try:
            headers = next(reader)
        except StopIteration:
            return
            
        headers_lower = [str(h).strip().lower() for h in headers]
        
        idx_map = {}
        for i, h in enumerate(headers_lower):
            if "amostra" in h or "sample" in h or "id" in h: idx_map["sample"] = i
            elif "tratamento" in h or "treatment" in h or "treat" in h: idx_map["treatment"] = i
            elif "known" in h or "inicial" in h: idx_map["known_cm2"] = i
            elif "remaining" in h or "restante" in h: idx_map["remaining_cm2"] = i
            elif "consumed" in h or "consumido" in h: idx_map["consumed_cm2"] = i
            elif "ppcm" in h or "px/cm" in h: idx_map["ppcm"] = i
            elif "foto" in h or "photo" in h: idx_map["photo_date"] = i
            elif "time" in h or "data" in h: idx_map["timestamp"] = i
            
        if "sample" not in idx_map:
            messagebox.showerror("Erro", "Coluna indicando 'Amostra' (ou Sample) não encontrada.")
            return
            
        count = 0
        for row in reader:
            if not row or len(row) <= idx_map["sample"]: continue
            
            s = row[idx_map["sample"]].strip()
            if not s: continue
            
            t = row[idx_map["treatment"]].strip() if "treatment" in idx_map and len(row) > idx_map["treatment"] else ""
            self.imported_samples.append((s, t))
            
            p_date = ""
            if "photo_date" in idx_map and len(row) > idx_map["photo_date"]:
                p_date = row[idx_map["photo_date"]]
                
            if s not in self.photo_metadata or not self.photo_metadata[s]:
                self.photo_metadata[s] = p_date
            
            if "known_cm2" in idx_map or "consumed_cm2" in idx_map:
                def parse_float(val):
                    try:
                        if not val: return ""
                        return float(val.replace(',', '.'))
                    except ValueError:
                        return val

                res_row = {
                    "sample": s,
                    "treatment": t,
                    "known_cm2": parse_float(row[idx_map["known_cm2"]]) if "known_cm2" in idx_map and len(row) > idx_map["known_cm2"] else "",
                    "remaining_cm2": parse_float(row[idx_map["remaining_cm2"]]) if "remaining_cm2" in idx_map and len(row) > idx_map["remaining_cm2"] else "",
                    "consumed_cm2": parse_float(row[idx_map["consumed_cm2"]]) if "consumed_cm2" in idx_map and len(row) > idx_map["consumed_cm2"] else "",
                    "ppcm": parse_float(row[idx_map["ppcm"]]) if "ppcm" in idx_map and len(row) > idx_map["ppcm"] else "",
                    "timestamp": row[idx_map["timestamp"]] if "timestamp" in idx_map and len(row) > idx_map["timestamp"] else "",
                    "photo_date": p_date
                }
                self.results.append(res_row)

            count += 1
            
        samples = list(dict.fromkeys(item[0] for item in self.imported_samples))
        treats = list(dict.fromkeys(item[1] for item in self.imported_samples if item[1]))
        
        self.cb_sample['values'] = samples
        self.cb_treat['values'] = treats
        
        if samples:
            self.var_sample.set(samples[0])
        if treats:
            self.var_treat.set(treats[0])
            
        self._refresh_trees()
            
        self._set_status(f"{count} linhas importadas.")
        messagebox.showinfo("Sucesso", f"{count} amostras carregadas com sucesso para a tabela e listas.")

    def _refresh_trees(self):
        self.tree.delete(*self.tree.get_children())
        for r in self.results:
            self.tree.insert("", "end", values=tuple(r.get(c, "") for c in (
                "sample", "treatment", "known_cm2", "remaining_cm2",
                "consumed_cm2", "ppcm", "timestamp", "photo_date")))
        self._update_metadata_tree()

    def _update_metadata_tree(self):
        self.meta_tree.delete(*self.meta_tree.get_children())
        for sample_id, p_date in self.photo_metadata.items():
            self.meta_tree.insert("", "end", values=(sample_id, p_date))

    def _on_meta_select(self, e):
        sel = self.meta_tree.selection()
        if sel:
            item = self.meta_tree.item(sel[0])
            self.var_edit_date.set(item["values"][1])

    def _update_selected_meta(self):
        sel = self.meta_tree.selection()
        if not sel: return
        sample_id = str(self.meta_tree.item(sel[0])["values"][0])
        new_date = self.var_edit_date.get()
        self.photo_metadata[sample_id] = new_date
        
        for r in self.results:
            if r["sample"] == sample_id:
                r["photo_date"] = new_date
                
        self._update_metadata_tree()
        self._refresh_trees()
        self._set_status(f"Observação de {sample_id} atualizada.")

    def _get_photo_date(self, path):
        try:
            img = Image.open(path)
            exif = img._getexif()
            if exif and 36867 in exif:
                return exif[36867]
        except Exception:
            pass
        return ""

    def _pick_image(self):
        p = filedialog.askopenfilename(
            title="Selecione uma imagem",
            filetypes=[("Imagens", "*.jpg *.jpeg *.png *.tif *.tiff *.bmp"),
                       ("Todos", "*.*")])
        if p:
            self.var_img.set(p)
            self.cur_path = p
            self.cur_calib = None
            self.cur_brush_px = None
            self.cur_remaining = self.cur_consumed = self.cur_ppcm = None
            self.lbl_calib.config(text="—"); self.lbl_meas.config(text="—")
            self.lbl_result.config(text="—")
            self.cur_photo_date = self._get_photo_date(p)

    def _check_path(self):
        if not self.cur_path or not Path(self.cur_path).exists():
            messagebox.showerror("Erro", "Selecione a imagem primeiro.")
            return False
        return True

    def _mark_calib(self):
        if not self._check_path(): return
        try:
            ka = float(self.var_known_area.get()); assert ka > 0
        except Exception:
            messagebox.showerror("Erro", "Área conhecida inválida."); return
        roi = ask_roi(self, self.cur_path, "ROI de calibração",
            f"Desenhe o contorno do que originalmente era a folha (área = {ka} cm²). "
            f"Ajuste cada vértice livremente; adicione mais clicando nas arestas.")
        if roi:
            self.cur_calib = roi
            self.lbl_calib.config(
                text=f"OK: {roi['n_vertices']} vértices, área = {int(roi['area_orig_px']):,} px²")

    def _mark_meas_brush(self):
        if not self._check_path(): return
        if not self.cur_calib:
            messagebox.showwarning("Atenção",
                "Marque o ROI de calibração antes — assim o pincel mostra a área "
                "em cm² em tempo real.")
        try:
            known = float(self.var_known_area.get())
            ppcm = ((self.cur_calib["area_orig_px"] / known) ** 0.5
                    if self.cur_calib else None)
        except Exception:
            ppcm = None

        roi_poly = self.cur_calib["poly_orig"] if self.cur_calib else None

        result = ask_brush(self, self.cur_path,
            "Pincel manual — pintar áreas CONSUMIDAS",
            "Pinte as regiões da folha que foram consumidas. "
            "Esquerdo = pintar, Direito = apagar, Roda do mouse / [ ] = tamanho do pincel, "
            "Z = desfazer. Use o painel 'ROI de calibração' para ver/ocultar e limitar o pincel ao ROI.",
            ppcm=ppcm,
            roi_poly=roi_poly)
        if result:
            self.cur_brush_px = int(result["painted_pixels_orig"])
            self.lbl_meas.config(text=f"OK: {self.cur_brush_px:,} px pintados")

    def _completely_devoured(self):
        try:
            known = float(self.var_known_area.get())
        except ValueError:
            messagebox.showerror("Erro", "Área conhecida inválida."); return
            
        self.cur_consumed = known
        self.cur_remaining = 0.0
        self.cur_brush_px = 0
        if self.cur_calib:
            self.cur_ppcm = (self.cur_calib["area_orig_px"] / known) ** 0.5
        else:
            self.cur_ppcm = None
            
        self.lbl_result.config(
            text=(f"Consumido: {known:.3f} cm²  |  Restante: 0.000 cm²\n"
                  f"(Folha completamente devorada)"))
        self._set_status("Folha completamente devorada calculada.")
        
        if self.var_auto_add.get():
            self._add_result()

    def _calculate(self):
        if not self._check_path(): return
        if not self.cur_calib:
            messagebox.showwarning("Faltando", "Marque o ROI de calibração."); return
        if self.cur_brush_px is None:
            messagebox.showwarning("Faltando", "Use o pincel para pintar o consumo."); return
        try:
            known = float(self.var_known_area.get())
        except ValueError:
            messagebox.showerror("Erro", "Área conhecida inválida."); return

        ppcm = (self.cur_calib["area_orig_px"] / known) ** 0.5
        self.cur_ppcm = ppcm
        consumed_cm2 = self.cur_brush_px / (ppcm ** 2)
        remaining = known - consumed_cm2
        self.cur_remaining = remaining
        self.cur_consumed = consumed_cm2
        self.lbl_result.config(
            text=(f"Consumido: {consumed_cm2:.3f} cm²  |  Restante: {remaining:.3f} cm²\n"
                  f"({self.cur_brush_px:,} px pintados, calib = {ppcm:.1f} px/cm)"))
        self._set_status("Folha medida.")
        
        if self.var_auto_add.get():
            self._add_result()

    def _add_result(self):
        sample = self.var_sample.get().strip()
        if not sample:
            messagebox.showwarning("Faltando", "Informe o ID da amostra."); return
        if self.cur_remaining is None:
            messagebox.showwarning("Faltando", "Calcule antes de adicionar."); return
        try:
            known = float(self.var_known_area.get())
        except ValueError:
            messagebox.showerror("Erro", "Área conhecida inválida."); return

        consumed = self.cur_consumed if self.cur_consumed is not None \
                   else known - self.cur_remaining
                   
        p_date = getattr(self, 'cur_photo_date', "")
        if sample in self.photo_metadata and self.photo_metadata[sample]:
            p_date = self.photo_metadata[sample]
            
        row = {
            "sample": sample, "treatment": self.var_treat.get().strip(),
            "known_cm2": round(known, 4),
            "remaining_cm2": round(self.cur_remaining, 4),
            "consumed_cm2": round(consumed, 4),
            "ppcm": (round(self.cur_ppcm, 2) if self.cur_ppcm else ""),
            "timestamp": datetime.now().isoformat(timespec="seconds"),
            "photo_date": p_date
        }
        
        self.results.append(row)
        self.tree.insert("", "end", values=tuple(row[c] for c in (
            "sample", "treatment", "known_cm2", "remaining_cm2",
            "consumed_cm2", "ppcm", "timestamp", "photo_date")))
            
        if sample not in self.photo_metadata or not self.photo_metadata[sample]:
            self.photo_metadata[sample] = p_date
        self._update_metadata_tree()
        
        self._set_status(f"Adicionado: {sample}.")
        self._clear_fields(keep_treatment=True)

    def _clear_fields(self, keep_treatment=False):
        self.var_sample.set("")
        if not keep_treatment: self.var_treat.set("")
        self.var_img.set("")
        for lbl in (self.lbl_calib, self.lbl_meas, self.lbl_result):
            lbl.config(text="—")
        self._reset_current()

    def _remove_selected(self):
        sel = self.tree.selection()
        if not sel: return
        for item in sel:
            idx = self.tree.index(item); self.tree.delete(item)
            try: self.results.pop(idx)
            except IndexError: pass
            
        self._update_metadata_tree()

    def _reset_all_data(self):
        if not self.results and not self.photo_metadata and not self.imported_samples:
            return
        if messagebox.askyesno("Limpar Tudo", "Deseja realmente apagar todos os resultados e observações para iniciar uma nova análise?"):
            self.results = []
            self.photo_metadata = {}
            self.imported_samples = []
            self.cb_sample['values'] = []
            self.cb_treat['values'] = []
            self.var_sample.set("")
            self.var_treat.set("")
            self.var_edit_date.set("")
            self._clear_fields()
            self._refresh_trees()
            self._set_status("Todos os dados foram apagados. Pronto para nova análise.")

    def _export_csv(self):
        if not self.results:
            messagebox.showinfo("Vazio", "Nenhum resultado."); return

        fmt = self._ask_export_format()
        if fmt is None: return

        path = filedialog.asksaveasfilename(
            title="Salvar CSV", defaultextension=".csv",
            filetypes=[("CSV", "*.csv")],
            initialfile=f"herbivoria_{datetime.now():%Y%m%d_%H%M}.csv")
        if not path: return

        cols = ["sample", "treatment", "known_cm2", "remaining_cm2",
                "consumed_cm2", "ppcm", "timestamp", "photo_date"]

        if fmt == "excel_br":
            sep = ";"; decimal_char = ","; write_sep_hint = True
        elif fmt == "excel_int":
            sep = ","; decimal_char = "."; write_sep_hint = False
        else:
            sep = ","; decimal_char = "."; write_sep_hint = False

        def fmt_value(key, val):
            if isinstance(val, float):
                s = f"{val}"
                return s.replace(".", decimal_char) if decimal_char != "." else s
            return val

        with open(path, "w", newline="", encoding="utf-8-sig") as fh:
            if write_sep_hint:
                fh.write(f"sep={sep}\n")
            w = csv.writer(fh, delimiter=sep,
                           quoting=csv.QUOTE_MINIMAL)
            w.writerow(cols)
            for r in self.results:
                w.writerow([fmt_value(c, r.get(c, "")) for c in cols])

        self._set_status(f"Exportado: {path}")
        messagebox.showinfo("OK",
            f"CSV salvo em:\n{path}\n\n"
            f"Formato: {fmt}\n"
            "Abra com Excel ou importe direto no GraphPad Prism.")

    def _ask_export_format(self):
        dlg = tk.Toplevel(self)
        dlg.title("Formato de exportação")
        dlg.transient(self); dlg.grab_set(); dlg.resizable(False, False)
        result = {"value": None}

        ttk.Label(dlg, padding=(16, 12),
                  text="Escolha o formato do CSV:",
                  font=("", 11, "bold")).pack(anchor="w")

        ttk.Label(dlg, padding=(16, 0),
                  wraplength=420, justify="left",
                  text=(
                      "• Excel (Brasil): separador ; e decimal vírgula. "
                      "Abre direto no Excel em PT-BR com cada coluna no lugar.\n"
                      "• Excel (internacional) / Google Sheets: separador , "
                      "e decimal ponto.\n"
                      "• GraphPad Prism: separador , e decimal ponto."
                  )).pack(anchor="w", pady=(0, 12))

        var = tk.StringVar(value="excel_br")
        for label, val in (
            ("Excel (Brasil) — recomendado para abrir no Excel PT-BR", "excel_br"),
            ("Excel internacional / Google Sheets",                    "excel_int"),
            ("GraphPad Prism",                                         "prism"),
        ):
            ttk.Radiobutton(dlg, text=label, variable=var, value=val,
                            padding=(20, 2)).pack(anchor="w")

        bar = ttk.Frame(dlg, padding=(16, 12)); bar.pack(fill="x")
        def confirm(): result["value"] = var.get(); dlg.destroy()
        def cancel():  dlg.destroy()
        ttk.Button(bar, text="Cancelar", command=cancel).pack(side="right")
        ttk.Button(bar, text="Exportar", command=confirm).pack(side="right", padx=6)

        dlg.bind("<Escape>", lambda e: cancel())
        dlg.bind("<Return>", lambda e: confirm())

        self.update_idletasks()
        dlg.update_idletasks()
        x = self.winfo_rootx() + (self.winfo_width() - dlg.winfo_width()) // 2
        y = self.winfo_rooty() + (self.winfo_height() - dlg.winfo_height()) // 2
        dlg.geometry(f"+{max(0, x)}+{max(0, y)}")

        self.wait_window(dlg)
        return result["value"]

    def _set_status(self, msg):
        self.status.set(msg); self.update_idletasks()

if __name__ == "__main__":
    App().mainloop()